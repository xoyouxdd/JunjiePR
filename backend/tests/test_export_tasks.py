"""Real export handlers with synthetic users; no deployment/database reuse."""
from test_rotation_temporary import app, setup, login, ok, TestClient, SessionLocal, Employee, UserAccount
from app.routers import export_tasks as tasks
from app.v2_models import AuditLog
from io import BytesIO
from zipfile import ZipFile
from datetime import date
import time
import json
import pytest
from PIL import Image


def wait(c, key):
    for _ in range(150):
        row=next(r for r in ok(c.get('/api/export-tasks'))['items'] if r['id']==key)
        if row['status'] in {'failed','completed'}:return row
        time.sleep(.05)
    pytest.fail('export worker did not finish')


def create(c, target, **kwargs):
    r=c.post('/api/export-tasks', params={'target':target}, **kwargs)
    assert r.status_code==202,r.text
    return r.json()


def test_actual_excel_and_owner_isolation_and_scope_revalidation():
    with TestClient(app) as gsm,TestClient(app) as cm:
        ok(login(gsm,'9730040'));ok(login(cm,'9730000'))
        job=create(gsm,'/api/statistics/export?month='+date.today().strftime('%Y-%m'))
        row=wait(gsm,job['id']);assert row['status']=='completed',row
        response=gsm.get(row['download_url']);assert response.status_code==200,response.text
        assert 'no-store' in response.headers['cache-control']
        with ZipFile(BytesIO(response.content)) as z:assert 'xl/workbook.xml' in z.namelist()
        assert not ok(cm.get('/api/export-tasks'))['items']
        assert cm.get(row['download_url']).status_code==404
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no='9730040').one();old=e.attraction_id;e.attraction_id=None;db.commit()
        assert gsm.get(row['download_url']).status_code==403
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no='9730040').one();e.attraction_id=old;db.commit()
        assert gsm.get(row['download_url']).status_code==200


@pytest.mark.parametrize('target',['https://example.org/api/statistics/export','//example.org/api/statistics/export','/api/me','/api/login','/api/rotation-board/me/act','/api/statistics/export#x','/api/statistics/export/../me'])
def test_no_arbitrary_proxy(target):
    with TestClient(app) as c:
        ok(login(c,'9730040'));assert c.post('/api/export-tasks',params={'target':target}).status_code==400


@pytest.mark.parametrize('target',['/api/statistics/export?month=2026-10','/api/pr-rankings/export?start_date=2026-10-01&end_date=2026-10-24','/api/hr-monthly-reports/export','/api/declaration-statistics/export'])
def test_frontline_cannot_enqueue(target):
    with TestClient(app) as c:
        ok(login(c,'9730000'));assert c.post('/api/export-tasks',params={'target':target}).status_code==403


def test_original_validation_error_and_credential_input_cleanup():
    with TestClient(app) as c:
        ok(login(c,'9730040'));job=create(c,'/api/statistics/export?month=broken')
        row=wait(c,job['id']);assert row['status']=='failed'
        assert not (tasks.Path(tasks._jobs[job['id']].directory.name)/'request').exists()
        assert c.get('/api/export-tasks/'+job['id']+'/file').status_code==409
        assert c.post('/api/export-tasks/'+job['id']+'/retry').status_code==409


def test_statistics_retry_reuses_filters_with_one_job_and_one_audit(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from openpyxl import load_workbook
    from app.routers import statistics
    from app.v2_models import AuditLog

    save = statistics.save_export_workbook
    calls = []

    def interrupted_once(workbook, output):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('synthetic interruption before export audit')
        return save(workbook, output)

    monkeypatch.setattr(statistics, 'save_export_workbook', interrupted_once)
    month = date.today().strftime('%Y-%m')
    with SessionLocal() as db:
        circle = db.query(Employee).filter_by(employee_no='9730000').one().attraction_id
        audits_before = db.query(AuditLog).filter_by(action='导出统计数据').count()

    with TestClient(app) as gsm, TestClient(app) as cm:
        ok(login(gsm, '9730040'))
        ok(login(cm, '9730000'))
        first = create(gsm, f'/api/statistics/export?month={month}&attraction_id={circle}&keyword=9730000&title=CM')
        failed = wait(gsm, first['id'])
        assert failed['status'] == 'failed'
        assert failed['retry_filters'] == {'month': month, 'attraction_name': '热力追踪', 'keyword': '9730000'}
        assert failed['retry_task_id'] is None
        retry_url = '/api/export-tasks/' + first['id'] + '/retry'
        assert cm.post(retry_url).status_code == 404

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: gsm.post(retry_url, params={'target': '/api/statistics/export?keyword=9730001'}), range(2)))
        assert [response.status_code for response in responses] == [202, 202]
        second_id, duplicate_id = (response.json()['id'] for response in responses)
        assert second_id == duplicate_id != first['id']
        assert wait(gsm, first['id'])['retry_task_id'] == second_id
        completed = wait(gsm, second_id)
        assert completed['status'] == 'completed'
        assert len(calls) == 2

        response = gsm.get(completed['download_url'])
        assert response.status_code == 200
        book = load_workbook(BytesIO(response.content), read_only=True, data_only=True)
        try:
            info = {row[0]: row[1] for row in book['导出说明'].values if len(row) > 1}
            assert (info['统计月份'], info['景点圈'], info['员工搜索'], info['Title']) == (month, '热力追踪', '9730000', 'CM')
            rows = list(book['月度综合分明细'].values)
            assert [row[0] for row in rows[1:]] == ['9730000']
        finally:
            book.close()
        with SessionLocal() as db:
            assert db.query(AuditLog).filter_by(action='导出统计数据').count() == audits_before + 1


@pytest.mark.parametrize('target',[
    '/api/pr-rankings/export?start_date=2026-10-01&end_date=2026-10-24',
    '/api/declaration-statistics/export?month=2026-10',
])
def test_other_real_excel_renderers(target):
    with TestClient(app) as c:
        ok(login(c,'9730040'));row=wait(c,create(c,target)['id'])
        assert row['status']=='completed',row
        response=c.get(row['download_url']);assert response.status_code==200
        with ZipFile(BytesIO(response.content)) as z:assert 'xl/workbook.xml' in z.namelist()


def test_announcement_reports_match_direct_download_and_audit_once():
    with TestClient(app) as c, TestClient(app) as cm:
        ok(login(c, '9730040'))
        ok(login(cm, '9730000'))
        opts = ok(c.get('/api/announcements/options'))
        announcement = ok(c.post('/api/announcements', json={
            'title': '签收导出任务', 'summary': '保留导出授权', 'body': '用现有报表函数导出。',
            'category': opts['categories'][0], 'scope_kind': 'circle',
            'attraction_ids': opts['publisher']['circles'], 'tiers': ['frontline'],
            'confirmation_level': 1, 'effective_on': date.today().isoformat(),
            'request_key': 'export-task-announcement',
        }))
        base = f"/api/announcements/{announcement['id']}"
        for filename, expected_type in [
            ('export?format=pdf&version=1.0', 'application/pdf'),
            ('followup-export?version=1.0', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        ]:
            target = base + '/' + filename
            assert cm.post('/api/export-tasks', params={'target': target}).status_code == 403
            direct = c.get(target)
            assert direct.status_code == 200, direct.text
            with SessionLocal() as db:
                previous = db.query(AuditLog).filter_by(action='导出公告签收').count()
            task = wait(c, create(c, target)['id'])
            assert task['status'] == 'completed', task
            output = c.get(task['download_url'])
            assert output.status_code == 200, output.text
            assert output.headers['content-type'] == direct.headers['content-type'] == expected_type
            assert output.headers['content-disposition'] == direct.headers['content-disposition']
            if expected_type == 'application/pdf':
                assert output.content.startswith(b'%PDF-')
            else:
                with ZipFile(BytesIO(output.content)) as workbook:
                    assert 'xl/workbook.xml' in workbook.namelist()
            with SessionLocal() as db:
                assert db.query(AuditLog).filter_by(action='导出公告签收').count() == previous + 1


def test_real_pptx_preview_contract_is_not_bypassed():
    with TestClient(app) as c:
        ok(login(c,'9730040'))
        config={'month':date.today().strftime('%Y-%m'),'template':'minimal','sections':['overview','photos']}
        row=wait(c,create(c,'/api/hr-monthly-reports/export',data={'config':json.dumps(config)})['id'])
        assert row['status']=='failed' and '预览' in row['error'],row
        preview=ok(c.get('/api/hr-monthly-reports/preview',params={'month':config['month']}))
        config['preview_digest']=preview['digest']
        photo = BytesIO()
        Image.new('RGB', (30, 30), 'red').save(photo, format='PNG')
        row=wait(c,create(c,'/api/hr-monthly-reports/export',data={'config':json.dumps(config)},
                          files={'photos': ('activity.png', photo.getvalue(), 'image/png')})['id'])
        assert row['status']=='completed',row
        response=c.get(row['download_url']);assert response.status_code==200
        with ZipFile(BytesIO(response.content)) as z:assert 'ppt/presentation.xml' in z.namelist()
        with SessionLocal() as db:
            audit = db.query(AuditLog).filter_by(action='导出HR月报').order_by(AuditLog.id.desc()).first()
            assert json.loads(audit.after_json)['photo_count'] == 1


def test_expiration_and_disabled_account():
    with TestClient(app) as c:
        ok(login(c,'9730040'));job=create(c,'/api/statistics/export?month='+date.today().strftime('%Y-%m'));row=wait(c,job['id'])
        assert row['status']=='completed'
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no='9730040').one();a=db.query(UserAccount).filter_by(employee_id=e.id).one();a.enabled=False;db.commit()
        assert c.get(row['download_url']).status_code==401
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no='9730040').one();a=db.query(UserAccount).filter_by(employee_id=e.id).one();a.enabled=True;db.commit()
        tasks._jobs[job['id']].finished-=tasks.TTL+1
        assert c.get(row['download_url']).status_code==404


def test_bounded_queue_and_authentication_rechecked_before_execution(monkeypatch):
    submitted=[]
    monkeypatch.setattr(tasks._pool,'submit',lambda fn,*args:submitted.append((fn,args)))
    with TestClient(app) as c:
        ok(login(c,'9730040'))
        target='/api/statistics/export?month='+date.today().strftime('%Y-%m')
        first=create(c,target);second=create(c,target)
        assert c.post('/api/export-tasks',params={'target':target}).status_code==429
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no='9730040').one();old=e.attraction_id;e.attraction_id=None;db.commit()
        fn,args=submitted[0];fn(*args)
        assert tasks._jobs[first['id']].status=='failed'
        assert '权限或可见范围' in tasks._jobs[first['id']].error
        assert not (tasks.Path(tasks._jobs[first['id']].directory.name)/'request').exists()
        with SessionLocal() as db:
            e=db.query(Employee).filter_by(employee_no='9730040').one();e.attraction_id=old;db.commit()
        fn,args=submitted[1];fn(*args)
        assert tasks._jobs[second['id']].status=='completed'


def test_fifteen_concurrent_readers_during_real_export():
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    import json
    with TestClient(app) as c:
        ok(login(c,'9730040'))
        def reads(index):
            start=time.monotonic()
            path=['/health','/api/action-center','/api/rotation-board/me'][index%3]
            response=c.get(path)
            assert response.status_code==200,response.text
            return round((time.monotonic()-start)*1000,2)
        with ThreadPoolExecutor(max_workers=15) as pool:
            baseline=list(pool.map(reads,range(15)))
            job=create(c,'/api/statistics/export?month='+date.today().strftime('%Y-%m'))
            during=list(pool.map(reads,range(15)))
        row=wait(c,job['id']);assert row['status']=='completed',row
        root=Path(__file__).resolve().parents[2]/'.handover_review/task-workbench-20261024'
        root.mkdir(parents=True,exist_ok=True)
        (root/'concurrency.json').write_text(json.dumps({'scenario':'local synthetic data, 15 simultaneous HTTP readers, real Excel worker; not physical phones or production capacity',
            'baseline_ms':baseline,'during_export_ms':during,'export_seconds':row['elapsed_seconds'],'file_bytes':row['bytes']},indent=2),encoding='utf-8')


def test_only_failed_locked_export_is_retried_without_duplicate_audit(monkeypatch):
    from app.routers import statistics
    from app.v2_models import AuditLog
    from sqlalchemy.exc import OperationalError
    import sqlite3
    original=statistics.build_statistics_workbook
    calls=[]
    def locked_once(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:raise OperationalError('synthetic read-to-write conflict',{},sqlite3.OperationalError('database is locked'))
        return original(*args,**kwargs)
    monkeypatch.setattr(statistics,'build_statistics_workbook',locked_once)
    with TestClient(app) as c:
        ok(login(c,'9730040'))
        with SessionLocal() as db:before=db.query(AuditLog).filter_by(action='导出统计数据').count()
        row=wait(c,create(c,'/api/statistics/export?month='+date.today().strftime('%Y-%m'))['id'])
        assert row['status']=='completed',row
        assert len(calls)==2
        with SessionLocal() as db:assert db.query(AuditLog).filter_by(action='导出统计数据').count()==before+1


def test_rotation_poll_does_not_open_a_second_router_session(monkeypatch):
    from app.routers import rotation_board
    def forbidden():raise AssertionError('rotation auth and business must share the request session')
    monkeypatch.setattr(rotation_board,'SessionLocal',forbidden)
    with TestClient(app) as c:
        ok(login(c,'9730040'))
        assert c.get('/api/rotation-board/me').status_code==200
        assert c.get('/api/rotation-board/board').status_code==200
