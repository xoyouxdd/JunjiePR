"""Disposable real API fixture for daily-plan browser regression."""
import os
from pathlib import Path
import sys
from datetime import date
from io import BytesIO

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ['RECOGNITION_V2_DATA_DIR'] = str(Path(sys.argv[1]).resolve() / 'data')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = 'daily-fixture-2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'daily-fixture-admin-2026'
os.environ['RECOGNITION_ENVIRONMENT'] = 'test'
from app.main import app
from app.rotation import service as S
from app.routers.rotation_board import actor, board_attraction_id, call, formal_db, manager
from app.runtime_environment import require_test_initialization
from fastapi import Depends
from fastapi.testclient import TestClient
from openpyxl import Workbook


require_test_initialization()
BOARD = '/api/rotation-board'
CLOCK = '/__fixture/rotation-clock'


@app.post(CLOCK, include_in_schema=False)
def fixture_clock(payload: dict, db=Depends(formal_db), user=Depends(manager)):
    with S.transaction(db):
        cfg = S.load_config(db, board_attraction_id(db, user))
        if payload['action'] == 'set':
            day = date.fromisoformat(payload['date']).isoformat()
            minute = S.action_time(payload['time'])
        else:
            assert payload['action'] == 'jump'
            clock = S.clock_state(cfg)
            assert clock['mode'] == 'sim'
            day, minute = clock['date'], clock['minute'] + float(payload['minutes'])
        S.set_sim_clock(cfg, day, minute, paused=True)
        S.mark_changed(db)
    return {'ok': True}


@app.post('/__fixture/rotation-draft', include_in_schema=False)
def fixture_reset_draft(payload: dict, db=Depends(formal_db), user=Depends(manager)):
    day = date.fromisoformat(payload['date']).isoformat()
    call(S.do_draft_action, db, board_attraction_id(db, user), actor(user),
         {'action': 'draft_generate', 'date': day, 'force': True})
    return {'ok': True}


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def edit_daily(client, day, action):
    url = f'{BOARD}/daily/{day}'
    revision = ok(client.get(url))['revision']
    ok(client.post(url, json={'action': action, 'revision': revision}))


with TestClient(app) as client:
    ok(client.post('/api/login', json={'employee_no': 'GSMTEST01', 'password': 'daily-fixture-2026'}))
    # Seed and publish before the automatic 03:00 start can race the setup.
    ok(client.post(CLOCK, json={'action': 'set', 'date': '2026-10-04', 'time': '02:30'}))
    ok(client.put(f'{BOARD}/config', json={'lines': [
        {'id': 'A', 'posts': [{'name': '入口分组'}, {'name': '安全观察'}, {'name': '出口协助'}]},
        {'id': 'B', 'posts': [{'name': '排队引导'}, {'name': '通道管理'}, {'name': '游客协助'}]}]}))
    wb = Workbook()
    wb.active.append(['工号', '姓名', '日期', '班次', '备注'])
    for day in ('2026-10-04', '2026-10-06'):
        for i, name in enumerate(['陈予安', '林知夏', '周明远', '许念初', '江云舟', '苏景和', '长姓名测试 Alexander', '午班测试']):
            wb.active.append([str(9100000+i), name, day, '07:15-16:15' if i < 7 else '12:00-21:00', ''])
    if os.environ.get('ROTATION_EARLY_POST_FIXTURE') == '1':
        wb.active.append(['9100008', '早班测试', '2026-10-06', '07:00-16:00', ''])
    buf = BytesIO()
    wb.save(buf)
    files = {'file': ('回归班表.xlsx', buf.getvalue(), 'application/octet-stream')}
    upload = f'{BOARD}/roster/upload'
    preview = ok(client.post(upload, files=files, data={'scope': 'week', 'preview': 'true'}))
    ok(client.post(upload, files=files, data={'scope': 'week', 'expected_digest': preview['digest'],
                                             'confirm_exceptions': 'true'}))
    edit_daily(client, '2026-10-04', 'draft_generate')
    edit_daily(client, '2026-10-04', 'publish')
    ok(client.post(CLOCK, json={'action': 'set', 'date': '2026-10-04', 'time': '07:30'}))
    ok(client.get(f'{BOARD}/board'))
    if os.environ.get('ROTATION_FORECAST_FIXTURE') != '1':
        ok(client.post(CLOCK, json={'action': 'set', 'date': '2026-10-05', 'time': '03:30'}))
        ok(client.get(f'{BOARD}/board'))

import uvicorn
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')
