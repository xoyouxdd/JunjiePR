"""Authenticated formal V2 routes, screen restrictions and personal receipts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from test_rotation_formal import (app, seed, formal_daytime_runtime, seed_today, login, ok,
                                 SessionLocal, Employee, RotationConfig, RotationEvent)
from app.rotation import service as S


@pytest.fixture
def today():
    return seed_today(rule_version=2)


def test_manager_can_set_four_late_shifts_for_current_workday(today):
    day = date.today().isoformat()
    config_path = '/api/rotation-board/config'
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        with S.transaction(db):
            row = S.day_row(db, today, day)
            state = S.day_state(row)
            state['lastTick'] = 720
            worker = next(p for p in state['persons'].values() if p['state'] == 'onpost')
            worker.update(start=480, end=735)
            template = state['persons']['9710000']
            for start, count in [(510, 2), (540, 2), (570, 1)]:
                for i in range(count):
                    pid = f'{start}{i:04}'
                    state['persons'][pid] = dict(template, pid=pid, state='other', start=start,
                                                 end=1200, assign=None)
            S.save_day(db, row, state)

    with TestClient(app) as manager, TestClient(app) as cm:
        login(manager, '9710020')
        login(cm, '9710000')
        section = ok(manager.get(config_path))['late_shifts']
        assert section['date'] == day and section['selected'] is None
        assert section['defaults'] == [570 * 60, 540 * 60]
        select = [start * 60 for start in (480, 510, 540, 570)]
        request = {'work_date': day, 'late_shifts': select}
        assert cm.put(config_path, json=request).status_code == 403
        assert manager.put(config_path, json={**request, 'work_date': '2000-01-01'}).status_code == 400
        for invalid in (select + [600 * 60], [select[0], select[0]], [1], [False]):
            assert manager.put(config_path, json={**request, 'late_shifts': invalid}).status_code == 400
        ok(manager.put(config_path, json=request))
        ok(manager.put(config_path, json={'settings': {'breakMin': 16}}))
        assert ok(manager.get(config_path))['late_shifts']['selected'] == select
        board = ok(manager.get('/api/rotation-board/board'))['day']
        assert next(p for p in board['persons'] if p['pid'] == worker['pid'])['preparing'] is None
        ok(manager.put(config_path, json={**request, 'late_shifts': None}))
        assert ok(manager.get(config_path))['late_shifts']['selected'] is None
        board = ok(manager.get('/api/rotation-board/board'))['day']
        assert next(p for p in board['persons'] if p['pid'] == worker['pid'])['preparing'] == '推下班'


def test_post_editing_allows_tr_but_rejects_member_and_screen(today):
    with TestClient(app) as tr, TestClient(app) as cm, TestClient(app) as screen:
        login(tr, '9710010'); login(cm, '9710000')
        ok(screen.post('/api/login', json={'employee_no': '6666666', 'password': '1243'}))
        day = ok(tr.get('/api/rotation-board/board'))['day']; post = day['lines'][0]['posts'][0]
        body = {'action': 'rename_post', 'post_key': post['key'], 'expected_name': post['name'], 'name': '恢复改名'}
        assert cm.post('/api/rotation-board/act', json=body).status_code == 403
        assert screen.post('/api/rotation-screen/act', json=body).status_code == 403
        ok(tr.post('/api/rotation-board/act', json=body))
        day = ok(tr.get('/api/rotation-board/board'))['day']
        body = {'action': 'post_order', 'line': 'A1', 'i': 0, 'target_i': 1, 'swapMode': 'posts',
                'expected_snapshot': day['forecastSnapshot']}
        assert cm.post('/api/rotation-board/act', json=body).status_code == 403
        assert screen.post('/api/rotation-screen/act', json=body).status_code == 403
        ok(tr.post('/api/rotation-board/act', json=body))


def test_post_edit_rejects_stale_board_snapshot(today):
    with TestClient(app) as operator:
        login(operator, '9710010')
        board_url = '/api/rotation-board/board'
        action_url = '/api/rotation-board/act'
        before = ok(operator.get(board_url))['day']
        line = before['lines'][0]
        body = {'action': 'post_order', 'line': line['id'], 'i': 0, 'target_i': 1, 'swapMode': 'posts'}
        post = line['posts'][0]
        ok(operator.post(action_url, json={'action': 'rename_post', 'post_key': post['key'],
                                          'expected_name': post['name'], 'name': '现场改名'}))
        current = ok(operator.get(board_url))['day']
        assert operator.post(action_url, json={**body, 'expected_snapshot': before['forecastSnapshot']}).status_code == 409
        assert ok(operator.get(board_url))['day']['lines'] == current['lines']
        ok(operator.post(action_url, json={**body, 'expected_snapshot': current['forecastSnapshot']}))
        after = ok(operator.get(board_url))['day']['lines'][0]['posts']
        assert [post['name'] for post in after[:2]] == [line['posts'][1]['name'], '现场改名']


def test_cm_confirms_own_forecast_and_receipt_is_exactly_once(today):
    with TestClient(app) as cm, TestClient(app) as tr:
        login(cm, '9710000'); login(tr, '9710010')
        own = ok(cm.get('/api/rotation-board/me'))['person']
        assert own['task']['status'] == 'forecast' and own['canDepart']
        body = {'action': 'depart', 'confirmation_token': own['confirmationTokens']['depart'],
                'expected_line': own['assign']['line']}
        result = ok(cm.post('/api/rotation-board/me/act', json=body))
        assert result['executed'] and result['result']['line'] == 'A1'
        assert ok(cm.post('/api/rotation-board/me/act', json=body))['duplicate']
        assert ok(cm.get('/api/rotation-board/me'))['person']['state'] == 'onpost'
        assert cm.post('/api/rotation-board/act', json={'action': 'pool', 'pid': '9710010', 'pool': 'rest'}).status_code == 403
        board = ok(tr.get('/api/rotation-board/board'))['day']
        assert board['ruleVersion'] == 2 and board['lines'][0]['posts'][0]['occ'] == '9710000'


def test_personal_todo_reads_previous_commit_while_writer_holds_rotation_lock(today):
    day = date.today().isoformat()
    with TestClient(app) as cm, ThreadPoolExecutor(max_workers=1) as pool:
        login(cm, '9710000')
        before = ok(cm.get('/api/rotation-board/me'))['person']
        with SessionLocal() as db:
            db.info['rotation_formal'] = True
            with S.transaction(db):
                row = S.day_row(db, today, day)
                state = S.day_state(row)
                p = state['persons']['9710000']
                p.update(state='pending', assign=None, readyAt=None)
                S.save_day(db, row, state)
                db.flush()
                during = ok(pool.submit(cm.get, '/api/rotation-board/me').result(timeout=5))['person']
                assert during['state'] == before['state']
                assert during['task'] == before['task']
                assert not during['canArrive']
        after = ok(cm.get('/api/rotation-board/me'))['person']
        assert after['state'] == 'pending' and after['canArrive'] and after['task'] is None


def test_screen_cannot_drag_or_modify_shift_or_sort_time(today):
    with TestClient(app) as screen:
        ok(screen.post('/api/login', json={'employee_no': '6666666', 'password': '1243'}))
        day = ok(screen.get('/api/rotation-screen/state'))['day']
        assert day['ruleVersion'] == 2
        result = screen.post('/api/rotation-screen/act', json={'action': 'place', 'pid': '9710000',
                             'line': 'A1', 'i': 0, 'place_mode': 'swap', 'expected_snapshot': day['forecastSnapshot']})
        assert result.status_code==403
        after = ok(screen.get('/api/rotation-screen/state'))['day']
        assert after['lines']==day['lines']
        assert screen.post('/api/rotation-screen/act',json={'action':'pool','pid':'9710000','pool':'rest'}).status_code==403
        assert screen.post('/api/rotation-screen/act', json={'action': 'sort_time', 'line': 'A1', 'time': '00:00:00'}).status_code == 403
        assert screen.post('/api/rotation-screen/act', json={'action': 'shift', 'pid': '9710000', 'start': '08:00', 'end': '20:00'}).status_code == 403


def test_tr_queries_across_circles_max_three_and_adds_temporary_record_only(today):
    with TestClient(app) as tr:
        login(tr, '9710010')
        matches = ok(tr.get('/api/rotation-board/people-search', params={'q': '97100'}))['people']
        assert 0 < len(matches) <= 3
        exact = ok(tr.get('/api/rotation-board/people-search', params={'q': '9710001'}))['people']
        assert exact[0]['pid'] == '9710001' and exact[0]['attraction'] == '矮人迷宫'
        with SessionLocal() as db:
            before = db.query(Employee).count()
        ok(tr.post('/api/rotation-board/act', json={'action': 'add_person', 'temporary': True, 'name': '现场伙伴',
                                                  'start': '00:00', 'end': '23:59'}))
        day = ok(tr.get('/api/rotation-board/board'))['day']
        p = next(p for p in day['persons'] if p['name'] == '现场伙伴')
        assert p['temporary'] and p['state'] == 'pending'
        assert p['pid'].startswith('temporary:')
        with SessionLocal() as db:
            assert db.query(Employee).count() == before


def test_live_add_numbered_person_uses_existing_login_or_creates_temporary(today, monkeypatch):
    from app.rotation.temporary import TemporaryMember
    from app.v2_models import UserAccount

    path = '/api/rotation-board/act'
    preview_path = '/api/rotation-board/people-preview'
    base = {'action': 'add_person', 'temporary': True, 'start': '09:00', 'end': '23:59'}
    new = {**base, 'pid': '09730151', 'name': '现场临时有工号'}
    with TestClient(app) as manager, TestClient(app) as worker, TestClient(app) as cross:
        login(manager, '9710020')
        draft = ok(manager.post(preview_path, json=new))
        assert draft['initial_pin'] and draft['identity']['temporary']
        add = {**new, 'expected_identity': draft['digest']}
        assert manager.post(path, json={**add, 'end': '23:58'}).status_code == 409

        sync_notices = S.sync_notices

        def fail_notice_sync(db, aid, state, eng, now):
            if '9730151' in eng.P:
                assert db.query(TemporaryMember).filter_by(employee_no='9730151').first() is not None
                raise S.ActionError('模拟待办保存失败')
            return sync_notices(db, aid, state, eng, now)

        with monkeypatch.context() as patch:
            patch.setattr(S, 'sync_notices', fail_notice_sync)
            assert manager.post(path, json=add).status_code == 400
        with SessionLocal() as db:
            assert db.query(TemporaryMember).filter_by(employee_no='9730151').first() is None

        ok(manager.post(path, json=add))
        assert manager.post(path, json=add).status_code == 409
        ok(worker.post('/api/login', json={'employee_no': '9730151', 'password': '0151'}))
        assert ok(worker.get('/api/me'))['rotation_temporary']
        mine = ok(worker.get('/api/rotation-board/me'))
        assert mine['person']['pid'] == '9730151' and mine['person']['canArrive']
        assert any(n['kind'] == 'arrive' and n['status'] == 'open' for n in mine['notices'])
        ok(worker.post('/api/rotation-board/me/act', json={'action': 'arrive',
            'confirmation_token': mine['person']['confirmationTokens']['arrive']}))

        existing = {**base, 'pid': '9710001', 'name': '现场输入姓名'}
        preview = ok(manager.post(preview_path, json=existing))
        assert preview['identity']['cross_circle'] and not preview['initial_pin']
        ok(manager.post(path, json={**existing, 'expected_identity': preview['digest']}))
        login(cross, '9710001')
        assert ok(cross.get('/api/me'))['attraction_id'] != today
        mine = ok(cross.get('/api/rotation-board/me'))
        assert mine['attraction_id'] == today and mine['person']['name'] == preview['identity']['name']
        assert any(n['kind'] == 'arrive' and n['status'] == 'open' for n in mine['notices'])
        with SessionLocal() as db:
            assert db.query(UserAccount).filter_by(login_account='9730151').first() is None
            assert db.query(TemporaryMember).filter_by(employee_no='9710001').first() is None


def test_live_add_reuses_temporary_credentials_and_confirms_disabled_account(today):
    from app.rotation.temporary import TemporaryMember
    from app.v2_crypto import hash_password
    from app.v2_models import UserAccount

    original_hash = hash_password('existing-rotation-password')
    with SessionLocal() as db:
        expired = datetime.fromisoformat(date.today().isoformat())
        db.add(TemporaryMember(employee_no='9730152', name='已登记临时人员', attraction_id=today,
                               password_hash=original_hash, valid_until=expired, enabled=True))
        db.query(UserAccount).filter_by(login_account='9710031').one().enabled = False
        db.commit()

    path = '/api/rotation-board/act'
    preview_path = '/api/rotation-board/people-preview'
    base = {'action': 'add_person', 'temporary': True, 'start': '09:00', 'end': '01:00'}
    with TestClient(app) as manager, TestClient(app) as temp, TestClient(app) as disabled:
        login(manager, '9710020')
        reuse = {**base, 'pid': '9730152', 'name': '已登记临时人员'}
        check = ok(manager.post(preview_path, json=reuse))
        assert check['reuse_account'] and not check['initial_pin']
        ok(manager.post(path, json={**reuse, 'expected_identity': check['digest']}))
        ok(temp.post('/api/login', json={'employee_no': '9730152', 'password': 'existing-rotation-password'}))
        assert ok(temp.get('/api/rotation-board/me'))['person']['canArrive']

        exception = {**base, 'pid': '9710031', 'name': '停用员工'}
        check = ok(manager.post(preview_path, json=exception))
        assert '账号停用' in check['identity']['issues'] and not check['initial_pin']
        add = {**exception, 'expected_identity': check['digest']}
        assert manager.post(path, json=add).status_code == 409
        ok(manager.post(path, json={**add, 'confirm_exceptions': True}))
        login(disabled, '9710031')
        assert ok(disabled.get('/api/me'))['rotation_temporary']
        assert ok(disabled.get('/api/rotation-board/me'))['person']['canArrive']
        with SessionLocal() as db:
            assert not db.query(UserAccount).filter_by(login_account='9710031').one().enabled
            member = db.query(TemporaryMember).filter_by(employee_no='9730152').one()
            assert member.password_hash == original_hash
            assert member.valid_until == expired + timedelta(days=1, hours=1)


def test_timeout_is_personal_and_manual_pool_move_does_not_claim_execution(today):
    with TestClient(app) as manager:
        login(manager, '9710020')
        ok(manager.post('/api/rotation-board/act', json={'action': 'pool', 'pid': '9710000', 'pool': 'rest'}))
        ok(manager.post('/api/rotation-board/act', json={'action': 'break_time', 'pid': '9710000', 'remaining': 0}))
        day = ok(manager.get('/api/rotation-board/board'))['day']
        p = next(p for p in day['persons'] if p['pid'] == '9710000')
        assert p['canDepart']
        ok(manager.post('/api/rotation-board/act', json={'action': 'pool', 'pid': '9710000', 'pool': 'away'}))
        day = ok(manager.get('/api/rotation-board/board'))['day']
        p = next(p for p in day['persons'] if p['pid'] == '9710000')
        assert p['state'] == 'away' and p['assign'] is None
        ok(manager.post('/api/rotation-board/act', json={'action': 'back', 'pid': '9710000'}))
        data = ok(manager.get('/api/rotation-board/board'))
        p = next(p for p in data['day']['persons'] if p['pid'] == '9710000')
        assert p['state'] == 'rest' and p['readyAt'] - data['clock']['minute'] == pytest.approx(15, abs=.05)


def test_batch_sign_in_is_shared_by_screen_and_personal_todo(today):
    day = date.today().isoformat()
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        with S.transaction(db):
            row = S.day_row(db, today, day)
            state = S.day_state(row)
            state['lastTick'] = 719
            p = state['persons']['9710000']
            p.update(state='notyet', start=735, readyAt=None, assign=None)
            for i in range(15):
                pid = f'8990{i:03}'
                state['persons'][pid] = dict(p, pid=pid, name=pid, forecastOrder=pid)
            S.save_day(db, row, state)

    with TestClient(app) as cm, TestClient(app) as screen:
        login(cm, '9710000')
        ok(screen.post('/api/login', json={'employee_no': '6666666', 'password': '1243'}))
        S.tick_attraction(today, formal=True)
        mine = ok(cm.get('/api/rotation-board/me'))['person']
        assert any(item['type'] == 'rotation_step' for item in ok(cm.get('/api/action-center'))['items'])
        view = ok(screen.get('/api/rotation-screen/state'))['day']
        everyone = {p['pid']: p for p in view['persons']}
        assert everyone[mine['pid']]['canBatchCheckIn'] and mine['canBatchCheckIn']
        other = everyone['8990000']
        payload = {'action': 'batch_check_in', 'pid': other['pid'],
                   'confirmation_token': other['confirmationTokens']['batch_check_in']}
        assert cm.post('/api/rotation-board/me/act', json=payload).status_code == 403
        assert ok(screen.post('/api/rotation-screen/act', json=payload))['executed']
        own = {'action': 'batch_check_in', 'pid': mine['pid'],
               'confirmation_token': mine['confirmationTokens']['batch_check_in']}
        assert ok(cm.post('/api/rotation-board/me/act', json=own))['executed']
        assert ok(screen.post('/api/rotation-screen/act', json=own))['duplicate']
        assert ok(cm.get('/api/rotation-board/me'))['person']['batchCheckedIn']


def test_overnight_personal_confirmation_receipt_uses_workday_not_calendar_date(today, monkeypatch):
    previous = (date.today() - timedelta(days=1)).isoformat()
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        with S.transaction(db):
            row = S.day_row(db, today, date.today().isoformat())
            state = S.day_state(row)
            state.update(date=previous, lastTick=1450)
            for p in state['persons'].values():
                p['end'] = 1600
            state['persons']['9710000'].update(state='rest', readyAt=1450, timeoutAt=1453, assign=None)
            row.work_date = previous
            S.save_day(db, row, state)
    monkeypatch.setattr(S, 'clock_now', lambda cfg: (previous, 1450))
    with TestClient(app) as cm:
        login(cm, '9710000')
        data = ok(cm.get('/api/rotation-board/me'))
        assert data['clock']['date'] == previous
        p = data['person']
        result = ok(cm.post('/api/rotation-board/me/act', json={
            'action': 'depart', 'expected_line': p['assign']['line'],
            'confirmation_token': p['confirmationTokens']['depart']}))
        assert result['executed']
        receipt = cm.get('/api/rotation-board/confirmation', params={
            'date': previous, 'token': p['confirmationTokens']['depart']})
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()['confirmed']
