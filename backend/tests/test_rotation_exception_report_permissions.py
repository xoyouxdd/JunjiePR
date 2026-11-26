"""Formal HTTP boundary: private reports are management-only on every refresh."""
from datetime import date
import pytest
from fastapi.testclient import TestClient

from test_rotation_formal import seed, formal_daytime_runtime, seed_today, app, SessionLocal, S, login, ok

SECRET = 'private-exception-reason-for-managers'


def private_day():
    aid = seed_today()
    with SessionLocal() as db:
        db.info['rotation_formal'] = True
        with S.transaction(db):
            row = S.day_row(db, aid, date.today().isoformat())
            state = S.day_state(row)
            state['exceptionReports'] = [{'pid': '9710000', 'name': 'employee', 'at': 700,
                                         'line': 'A1', 'status': 'reported', 'reason': SECRET}]
            S.save_day(db, row, state)
    return aid


@pytest.mark.parametrize('index', range(6))
def test_private_report_role_boundary_on_initial_and_repeated_board_reads(index):
    private_day()
    with TestClient(app) as manager, TestClient(app) as viewer:
        login(manager, '9710040')
        assert SECRET in manager.get('/api/rotation-board/board').text  # Warm shared cache as GSM.
        login(viewer, f'97100{index}0')
        for _ in range(2):  # SSE only signals version; polling uses this same endpoint.
            response = viewer.get('/api/rotation-board/board')
            data = ok(response)
            if index >= 2:
                assert data['day']['exceptionReports'][0]['reason'] == SECRET
            else:
                assert 'exceptionReports' not in data['day']
                assert SECRET not in response.text
        assert SECRET in manager.get('/api/rotation-board/board').text  # Redaction never poisons cached management data.
        assert SECRET not in viewer.get('/api/rotation-board/me').text


def test_screen_device_cannot_read_private_reports_but_managers_still_can():
    private_day()
    with TestClient(app) as screen, TestClient(app) as manager:
        ok(screen.post('/api/login', json={'employee_no': '6666666', 'password': '1243'}))
        for _ in range(2):
            response = screen.get('/api/rotation-screen/state')
            assert 'exceptionReports' not in ok(response)['day']
            assert SECRET not in response.text
        login(manager, '9710020')
        assert SECRET in manager.get('/api/rotation-board/board').text
