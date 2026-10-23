"""Disposable real API fixture for daily-plan browser regression."""
import os
from pathlib import Path
import sys
from io import BytesIO

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ['RECOGNITION_V2_DATA_DIR'] = str(Path(sys.argv[1]).resolve() / 'data')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = 'daily-fixture-2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'daily-fixture-admin-2026'
os.environ['RECOGNITION_ENVIRONMENT'] = 'test'
from app.main import app
from fastapi.testclient import TestClient
from openpyxl import Workbook


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


with TestClient(app) as client:
    ok(client.post('/api/login', json={'employee_no': 'GSMTEST01', 'password': 'daily-fixture-2026'}))
    ok(client.post('/api/rotation/enter', json={'account': '7777777'}))
    ok(client.post('/api/rotation/test-clock', json={'action': 'set', 'date': '2026-10-04', 'time': '03:30'}))
    ok(client.put('/api/rotation/config', json={'lines': [
        {'id': 'A', 'posts': [{'name': '入口分组'}, {'name': '安全观察'}, {'name': '出口协助'}]},
        {'id': 'B', 'posts': [{'name': '排队引导'}, {'name': '通道管理'}, {'name': '游客协助'}]}]}))
    wb = Workbook()
    wb.active.append(['工号', '姓名', '日期', '班次', '备注'])
    for day in ('2026-10-04', '2026-10-06'):
        for i, name in enumerate(['陈予安', '林知夏', '周明远', '许念初', '江云舟', '苏景和', '长姓名测试 Alexander', '午班测试']):
            wb.active.append([str(9100000+i), name, day, '07:15-16:15' if i < 7 else '12:00-21:00', ''])
    buf = BytesIO(); wb.save(buf)
    ok(client.post('/api/rotation/roster/upload', files={'file': ('模拟班表.xlsx', buf.getvalue(), 'application/octet-stream')}, data={'scope': 'week'}))
    ok(client.post('/api/rotation/draft', json={'action': 'draft_generate', 'date': '2026-10-04'}))
    ok(client.post('/api/rotation/draft', json={'action': 'publish', 'date': '2026-10-04'}))
    ok(client.post('/api/rotation/test-clock', json={'action': 'set', 'date': '2026-10-04', 'time': '07:30'}))
    ok(client.get('/api/rotation/board'))
    ok(client.post('/api/rotation/test-clock', json={'action': 'set', 'date': '2026-10-05', 'time': '03:30'}))
    ok(client.get('/api/rotation/board'))

import uvicorn
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')
