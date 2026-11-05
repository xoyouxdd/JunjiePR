"""Authenticated formal V2 routes, screen restrictions and personal receipts."""
from datetime import date, timedelta

from fastapi.testclient import TestClient
import pytest

from test_rotation_formal import (app, seed, formal_daytime_runtime, seed_today, login, ok,
                                 SessionLocal, Employee, RotationConfig, RotationEvent)
from app.rotation import service as S


@pytest.fixture
def today():
    return seed_today(rule_version=2)


def test_post_editing_allows_tr_but_rejects_member_and_screen(today):
    with TestClient(app) as tr, TestClient(app) as cm, TestClient(app) as screen:
        login(tr, '9710010'); login(cm, '9710000')
        ok(screen.post('/api/login', json={'employee_no': '6666666', 'password': '1243'}))
        day = ok(tr.get('/api/rotation-board/board'))['day']; post = day['lines'][0]['posts'][0]
        body = {'action': 'rename_post', 'post_key': post['key'], 'expected_name': post['name'], 'name': '恢复改名'}
        assert cm.post('/api/rotation-board/act', json=body).status_code == 403
        assert screen.post('/api/rotation/screen/act', json=body).status_code == 403
        ok(tr.post('/api/rotation-board/act', json=body))
        day = ok(tr.get('/api/rotation-board/board'))['day']
        body = {'action': 'post_order', 'line': 'A1', 'i': 0, 'target_i': 1, 'swapMode': 'posts',
                'expected_snapshot': day['forecastSnapshot']}
        assert cm.post('/api/rotation-board/act', json=body).status_code == 403
        assert screen.post('/api/rotation/screen/act', json=body).status_code == 403
        ok(tr.post('/api/rotation-board/act', json=body))


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


def test_screen_cannot_drag_or_modify_shift_or_sort_time(today):
    with TestClient(app) as screen:
        ok(screen.post('/api/login', json={'employee_no': '6666666', 'password': '1243'}))
        day = ok(screen.get('/api/rotation/screen/state'))['day']
        assert day['ruleVersion'] == 2
        result = screen.post('/api/rotation/screen/act', json={'action': 'place', 'pid': '9710000',
                             'line': 'A1', 'i': 0, 'place_mode': 'swap', 'expected_snapshot': day['forecastSnapshot']})
        assert result.status_code==403
        after = ok(screen.get('/api/rotation/screen/state'))['day']
        assert after['lines']==day['lines']
        assert screen.post('/api/rotation/screen/act',json={'action':'pool','pid':'9710000','pool':'rest'}).status_code==403
        assert screen.post('/api/rotation/screen/act', json={'action': 'sort_time', 'line': 'A1', 'time': '00:00:00'}).status_code == 403
        assert screen.post('/api/rotation/screen/act', json={'action': 'shift', 'pid': '9710000', 'start': '08:00', 'end': '20:00'}).status_code == 403


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
        with SessionLocal() as db:
            assert db.query(Employee).count() == before


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
