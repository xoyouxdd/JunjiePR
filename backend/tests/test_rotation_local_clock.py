"""Isolated local extension; run as its own process like other rotation API suites."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from local_tools.rotation_clock.guard import prepare, require_local

DATA = prepare(Path(tempfile.mkdtemp(prefix='rotation-local-clock-')) / 'data')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = 'rotation-local-2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'rotation-local-admin-2026'
from app.main import app
from app.rotation import service as S
from app.rotation.namespace import FORMAL_TABLES as F
from app.v2_database import SessionLocal
from local_tools.rotation_clock.extension import install, control
from local_tools.rotation_clock.demo import seed

NORMAL_PATHS = {route.path for route in app.routes}
DAY = '2026-11-05'


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope='module', autouse=True)
def environment():
    install(app)
    seed(DATA)
    with TestClient(app):
        yield


def direct(body):
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        from app.rotation.access import rotation_attraction_id
        return control(db, rotation_attraction_id(db), body, 'local-test')


@pytest.fixture(autouse=True)
def fresh():
    preview = direct({'action': 'restart-preview'})
    direct({'action': 'restart', 'restart_token': preview['restart_token'], 'confirm_date': DAY})


@pytest.fixture
def manager():
    with TestClient(app) as client:
        ok(client.post('/api/login', json={'employee_no': 'GSMTEST01', 'password': 'rotation-local-2026'}))
        yield client


def clock(client, action, **body):
    return ok(client.post('/__local/rotation-clock', json={'action': action, **body}))


def board(client):
    return ok(client.get('/api/rotation-board/board'))


def act(client, **body):
    return ok(client.post('/api/rotation-board/act', json=body))


def ready(client, remaining=0, pool='rest'):
    data = board(client)
    p = next(p for p in data['day']['persons'] if p['state'] == 'onpost')
    act(client, action='pool', pid=p['pid'], pool=pool)
    act(client, action='break_time', pid=p['pid'], remaining=remaining)
    return p['pid']


def find(data, pid):
    return next(p for p in data['day']['persons'] if p['pid'] == pid)


def test_normal_entry_and_release_have_no_local_extension():
    assert not any(path.startswith('/__local/') for path in NORMAL_PATHS)
    assert '/__local/' not in (ROOT / 'backend/app/static/rotation-board.html').read_text(encoding='utf-8')
    spec = importlib.util.spec_from_file_location('local_release_check', ROOT / 'scripts/build_release.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    names = [name for _, name in module.release_sources()]
    assert not any('local_tools/' in name or 'rotation_clock/assets' in name or 'test_rotation_local_clock' in name for name in names)
    assert not any('local_tools' in p.read_text(encoding='utf-8') for p in [ROOT / 'backend/app/main.py', ROOT / 'backend/app/routers/v2.py'])


def test_guard_rejects_existing_database_and_production_flag(tmp_path, monkeypatch):
    existing = tmp_path / 'existing'; existing.mkdir()
    sentinel = existing / 'recognition_v2.db'; sentinel.write_bytes(b'unchanged')
    with pytest.raises(RuntimeError, match='独立空目录'):
        prepare(existing)
    assert sentinel.read_bytes() == b'unchanged'
    monkeypatch.setenv('RECOGNITION_ENVIRONMENT', 'production')
    with pytest.raises(RuntimeError, match='独立本地入口'):
        require_local()


def test_second_simulator_cannot_advance_same_directory(tmp_path):
    from local_tools.rotation_clock.guard import exclusive_data
    with exclusive_data(tmp_path):
        with pytest.raises(RuntimeError, match='已有模拟器'):
            with exclusive_data(tmp_path):
                pytest.fail('second simulator acquired the database')


def test_backdating_cannot_restore_expired_login(manager):
    from app.v2_models import UserSession, UserAccount
    from datetime import datetime, timedelta
    clock(manager, 'set', date='2020-01-01', time='00:00:00')
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(login_account='GSMTEST01').one()
        for session in db.query(UserSession).filter_by(account_id=account.id):
            session.expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()
    assert manager.get('/api/me').status_code == 401
    direct({'action': 'set', 'date': DAY, 'time': '04:00:00'})


def test_temporary_grants_expire_in_real_time_even_when_simulated_date_changes(manager, monkeypatch):
    from app.rotation import adjustment
    from app.v2_models import Employee
    from datetime import datetime, timedelta
    monkeypatch.setattr(adjustment, 'qualified', lambda *args, **kwargs: True)
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no='GSMTEST01').one()
        expired = SimpleNamespace(revoked=False, expires_at=datetime.now()-timedelta(minutes=1), employee_id=employee.id)
        valid = SimpleNamespace(revoked=False, expires_at=datetime.now()+timedelta(minutes=30), employee_id=employee.id)
        clock(manager, 'set', date='2020-01-01', time='00:00:00')
        assert not adjustment.live_grant(db, expired)
        clock(manager, 'set', date='2040-01-01', time='00:00:00')
        assert adjustment.live_grant(db, valid)
    clock(manager, 'set', date=DAY, time='04:00:00')


def test_apply_seconds_pauses_and_authentication_stays_on_real_time(manager):
    before = ok(manager.get('/api/me'))['employee_no']
    result = clock(manager, 'set', date=DAY, time='07:30:12', speed=10)['clock']
    assert result['minute'] == pytest.approx(450.2)
    assert result['paused'] and result['speed'] == 10
    assert result['calendarDate'] == DAY and result['calendarTime'] == '07:30:12'
    assert ok(manager.get('/api/me'))['employee_no'] == before
    future = clock(manager, 'set', date='2040-01-01', time='23:59:59')['clock']
    assert future['calendarDate'] == '2040-01-01' and future['calendarTime'] == '23:59:59'
    assert ok(manager.get('/api/me'))['employee_no'] == before
    # Restore this workday so the next fixture can explicitly restart it.
    clock(manager, 'set', date=DAY, time='07:30:12')


def test_paused_rest_meal_manual_placement_and_records_use_simulated_seconds(manager):
    clock(manager, 'set', date=DAY, time='07:30:12')
    pid = ready(manager, 1, 'meal')
    original = board(manager)['clock']['minute']
    clock(manager, 'jump', minutes=1)
    assert find(board(manager), pid)['canDepart']
    person = find(board(manager), pid)
    result = act(manager, action='depart', pid=pid, expected_line=person['assign']['line'],
                 confirmation_token=person['confirmationTokens']['depart'])
    assert result['executed']
    data = board(manager)
    other = next(p for p in data['day']['persons'] if p['state'] == 'onpost' and p['pid'] != pid)
    act(manager, action='pool', pid=other['pid'], pool='rest')
    data = board(manager)
    act(manager, action='place', pid=other['pid'], line='A', i=0, place_mode='swap', expected_snapshot=data['day']['forecastSnapshot'])
    assert board(manager)['clock']['minute'] == original + 1
    with SessionLocal() as db:
        events = db.query(F.RotationEvent).filter(F.RotationEvent.event_type == 'depart').all()
        assert events and all(e.minute == pytest.approx(original + 1) for e in events)


def test_fast_forward_does_not_confirm_and_exact_three_minutes_expires(manager):
    clock(manager, 'set', date=DAY, time='07:30:12')
    pid = ready(manager, 1)
    clock(manager, 'jump', minutes=1)
    p = find(board(manager), pid)
    assert p['canDepart'] and p['state'] != 'onpost'
    clock(manager, 'jump', minutes=1); clock(manager, 'jump', minutes=1)
    assert find(board(manager), pid)['state'] != 'exception'
    clock(manager, 'jump', minutes=1)
    assert find(board(manager), pid)['state'] == 'exception'
    assert find(board(manager), pid)['assign'] is None


@pytest.mark.parametrize('speed', [1, 5, 10, 30, 60])
def test_speed_background_tick_personal_and_screen_share_clock(manager, monkeypatch, speed):
    clock(manager, 'set', date=DAY, time='07:30:12')
    pid = ready(manager)
    with SessionLocal() as db:
        cfg = db.query(F.RotationConfig).one(); anchor = json.loads(cfg.clock_json)['real_ts']
        aid = cfg.attraction_id
    elapsed = [anchor]
    monkeypatch.setattr(S, 'time', SimpleNamespace(time=lambda: elapsed[0]))
    clock(manager, 'speed', speed=speed); clock(manager, 'resume')
    elapsed[0] += 60 / speed
    S.tick_attraction(aid, formal=True)
    data = board(manager)
    assert data['clock']['minute'] == pytest.approx(451.2)
    with TestClient(app) as member, TestClient(app) as screen:
        ok(member.post('/api/login', json={'employee_no': pid, 'password': 'rotation-local-2026'}))
        ok(screen.post('/api/login', json={'employee_no': '6666666', 'password': '1243'}))
        assert ok(member.get('/api/rotation-board/me'))['clock']['minute'] == pytest.approx(451.2)
        assert ok(screen.get('/api/rotation/screen/state'))['clock']['minute'] == pytest.approx(451.2)
        clock(manager, 'pause'); elapsed[0] += 100
        S.tick_attraction(aid, formal=True)
        assert board(manager)['clock']['minute'] == pytest.approx(451.2)
        assert find(board(manager), pid)['state'] != 'onpost'
        clock(manager, 'resume'); elapsed[0] += 120 / speed
        S.tick_attraction(aid, formal=True)
        # Check persisted background result before querying a page.
        with SessionLocal() as db:
            row = db.query(F.RotationDay).filter_by(work_date=DAY).one()
            assert json.loads(row.state_json)['persons'][pid]['state'] == 'exception'


def test_two_people_confirm_separately_while_paused(manager):
    clock(manager, 'set', date=DAY, time='07:30:12')
    first = ready(manager); second = ready(manager)
    one = find(board(manager), first)
    result=act(manager, action='depart', pid=first, expected_line=one['assign']['line'], confirmation_token=one['confirmationTokens']['depart'])
    if result.get('line_changed'):
        assert find(board(manager),first)['state']=='rest'
        assert act(manager,action='depart',pid=first,expected_line=result['line'],confirmation_token=one['confirmationTokens']['depart'])['executed']
    data = board(manager)
    assert find(data, first)['state'] == 'onpost'
    assert find(data, second)['state'] != 'onpost'
    two = find(data, second)
    result=act(manager, action='depart', pid=second, expected_line=two['assign']['line'], confirmation_token=two['confirmationTokens']['depart'])
    if result.get('line_changed'):
        assert find(board(manager),second)['state']=='rest'
        assert act(manager,action='depart',pid=second,expected_line=result['line'],confirmation_token=two['confirmationTokens']['depart'])['executed']
    assert find(board(manager), second)['state'] == 'onpost'


def test_off_duty_relief_uses_normal_business_rules(manager):
    clock(manager, 'set', date=DAY, time='15:50:12')
    incoming = next(p for p in board(manager)['day']['persons'] if p['state'] == 'onpost')
    act(manager, action='shift', pid=incoming['pid'], start='07:15', end='20:00')
    pid = ready(manager)
    p = find(board(manager), pid)
    assert p['assign']['kind'] == 'off'
    target = p['assign']['target']
    result = act(manager, action='depart', pid=pid, expected_line=p['assign']['line'], confirmation_token=p['confirmationTokens']['depart'])
    assert result['executed'] and find(board(manager), target)['state'] == 'done'


def test_overnight_final_shift_and_rewind_do_not_resurrect_day(manager):
    clock(manager, 'set', date=DAY, time='23:59:12')
    clock(manager, 'jump', minutes=1)
    data = board(manager)
    assert data['clock']['date'] == DAY and data['clock']['calendarDate'] == '2026-11-06'
    assert data['clock']['minute'] == pytest.approx(1440.2)
    assert data['day']['status'] == 'live'
    clock(manager, 'set', date='2026-11-06', time='01:59:00')
    assert board(manager)['clock']['minute'] == 1559
    clock(manager, 'jump', minutes=1)
    ended = board(manager)
    assert ended['day']['status'] == 'ended' and ended['day']['rotationEnd'] == 1560
    clock(manager, 'set', date=DAY, time='07:30:12')
    back = board(manager)
    assert back['day']['status'] == 'ended'


def test_rewind_does_not_undo_executed_position_or_replay_cursor(manager):
    clock(manager, 'set', date=DAY, time='07:30:12')
    pid = ready(manager); p = find(board(manager), pid)
    act(manager, action='depart', pid=pid, expected_line=p['assign']['line'], confirmation_token=p['confirmationTokens']['depart'])
    data = board(manager)
    slots = [(l['id'], [p['occ'] for p in l['posts']]) for l in data['day']['lines']]
    clock(manager, 'set', date=DAY, time='07:20:00')
    back = board(manager)
    assert [(l['id'], [p['occ'] for p in l['posts']]) for l in back['day']['lines']] == slots
    with SessionLocal() as db:
        assert json.loads(db.query(F.RotationDay).filter_by(work_date=DAY).one().state_json)['lastTick'] == pytest.approx(450.2)


def other_data(db, aid):
    # Compare every table, excluding only the exact run's mutable result rows
    # and the explicitly controlled clock column.
    from app.database.connection import Base
    out = {}
    scoped = {getattr(F, name).__table__.name for name in ['RotationDay', 'RotationEvent', 'RotationSegment', 'RotationDuty', 'RotationNotice', 'RotationSnapshot']}
    for table in Base.metadata.sorted_tables:
        rows = db.execute(table.select()).mappings().all()
        if table.name in scoped:
            rows = [r for r in rows if r['attraction_id'] != aid or r['work_date'] != DAY]
        out[table.name] = [dict(r) for r in rows]
        if table.name == F.RotationConfig.__table__.name:
            for row in out[table.name]: row.pop('clock_json')
    return out


def test_restart_is_scoped_restores_initial_and_requires_two_confirmations(manager):
    clock(manager, 'set', date=DAY, time='07:30:12')
    ready(manager)
    with SessionLocal() as db:
        aid = db.query(F.RotationConfig).one().attraction_id
        if not db.query(F.RotationDay).filter_by(work_date='2026-11-04').first():
            db.add(F.RotationDay(attraction_id=aid, work_date='2026-11-04', status='ended', state_json='{"history":true}', version=1)); db.commit()
        before = other_data(db, aid)
        prior = json.loads(db.query(F.RotationDay).filter_by(work_date=DAY).one().state_json)
    invalid = manager.post('/__local/rotation-clock', json={'action': 'restart', 'confirm_date': DAY})
    assert invalid.status_code == 400
    preview = clock(manager, 'restart-preview')
    assert manager.post('/__local/rotation-clock', json={'action': 'restart', 'restart_token': preview['restart_token'], 'confirm_date': '2026-11-04'}).status_code == 400
    clock(manager, 'restart', restart_token=preview['restart_token'], confirm_date=DAY)
    with SessionLocal() as db:
        assert other_data(db, aid) == before
        after = json.loads(db.query(F.RotationDay).filter_by(work_date=DAY).one().state_json)
        assert after['initialDraft'] == prior['initialDraft']
        assert after['confirmationEpoch'] != prior['confirmationEpoch']
        assert not any(p['state'] == 'exception' for p in after['persons'].values())
    assert board(manager)['clock']['paused'] and board(manager)['clock']['speed'] == 1
    assert manager.post('/__local/rotation-clock', json={'action': 'restart', 'restart_token': preview['restart_token'], 'confirm_date': DAY}).status_code == 400


@pytest.mark.parametrize('action', ['jump', 'restart'])
def test_clock_commit_failure_rolls_back_time_and_runtime(manager, monkeypatch, action):
    clock(manager, 'set', date=DAY, time='07:30:12')
    before = board(manager)
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        aid = db.query(F.RotationConfig).one().attraction_id
        body = {'action': 'jump', 'minutes': 10}
        if action == 'restart':
            grant = control(db, aid, {'action': 'restart-preview'}, 'test')
            body = {'action': 'restart', 'restart_token': grant['restart_token'], 'confirm_date': DAY}
        def fail(): raise RuntimeError('commit fault')
        monkeypatch.setattr(db, 'commit', fail)
        with pytest.raises(RuntimeError, match='commit fault'):
            control(db, aid, body, 'test')
    assert board(manager)['clock']['minute'] == before['clock']['minute']
    assert board(manager)['day']['forecastSnapshot'] == before['day']['forecastSnapshot']


@pytest.mark.parametrize('body', [
    {'action': 'set', 'date': DAY, 'time': '07:30:60'}, {'action': 'set', 'date': 'bad', 'time': '07:30'},
    {'action': 'speed', 'speed': 0}, {'action': 'speed', 'speed': True}, {'action': 'speed', 'speed': 120},
    {'action': 'jump', 'minutes': True}, {'action': 'jump', 'minutes': -1}, {'action': 'jump', 'minutes': 11},
    {'action': 'next'}, {'action': 'reset'}, {'action': 'real'},
])
def test_invalid_or_automatic_shortcut_actions_rejected(manager, body):
    assert manager.post('/__local/rotation-clock', json=body).status_code == 400


def test_control_requires_manager_but_personal_clock_is_readable():
    with TestClient(app) as member, TestClient(app) as anonymous:
        ok(member.post('/api/login', json={'employee_no': '9720001', 'password': 'rotation-local-2026'}))
        assert member.post('/__local/rotation-clock', json={'action': 'jump', 'minutes': 1}).status_code == 403
        assert anonymous.post('/__local/rotation-clock', json={'action': 'jump', 'minutes': 1}).status_code == 401
        assert ok(member.get('/__local/rotation-clock/state'))['clock']['localSimulation']


def test_personal_clock_follows_authorized_roster_circle_instead_of_hr_home(manager):
    from app.v2_models import Employee, Attraction
    clock(manager, 'set', date=DAY, time='07:30:12')
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no='9720001').one()
        original = employee.attraction_id
        other = db.query(Attraction).filter_by(name='矮人迷宫').one().id
        employee.attraction_id = other; db.commit()
    try:
        with TestClient(app) as member:
            ok(member.post('/api/login', json={'employee_no': '9720001', 'password': 'rotation-local-2026'}))
            personal = ok(member.get('/api/rotation-board/me', params={'attraction_id': original}))
            local = ok(member.get('/__local/rotation-clock/state', params={'attraction_id': original}))
            assert local['clock']['minute'] == personal['clock']['minute'] == pytest.approx(450.2)
            assert local['attraction_id'] == original
            assert member.get('/__local/rotation-clock/state', params={'attraction_id': other}).status_code == 403
    finally:
        with SessionLocal() as db:
            db.query(Employee).filter_by(employee_no='9720001').one().attraction_id = original; db.commit()
