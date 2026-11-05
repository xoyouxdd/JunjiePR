"""Real ranking/export handlers with isolated circle HR accounts and synthetic records."""
import os
import tempfile
from datetime import date
from decimal import Decimal
from io import BytesIO
import json
import time

import pytest
from openpyxl import load_workbook

os.environ['RECOGNITION_V2_DATA_DIR']=tempfile.mkdtemp(prefix='hr-pr-ranking-')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='1234'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='HR123'

from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal
from app.v2_crypto import hash_password
from app.v2_models import Attraction, Employee, EmployeeRoleAssignment, Role, UserAccount, RecognitionRecord, RecognitionType, AuditLog

ACCOUNTS=['HR-HEAT','HR-DWARF','HR-BEAR']
CATEGORIES=['overall','recognition','deduction','absence','leader','gsm_leader']
BASE={'start_date':date.today().replace(day=1).isoformat(),'end_date':date.today().isoformat(),'page_size':100}


def login(c,no):
    c.post('/api/logout')
    response=c.post('/api/login',json={'employee_no':no,'password':'HR123' if no=='HR01' else '1234'})
    assert response.status_code==200,response.text


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        with SessionLocal() as db:
            for account in db.query(UserAccount).filter(UserAccount.login_account.in_(ACCOUNTS)):
                account.enabled=True;account.must_change_password=False;account.password_hash=hash_password('1234')
            circles=db.query(Attraction).filter_by(active=True,employee_circle=True).all()
            roles={r.code:r for r in db.query(Role)}
            kind=db.query(RecognitionType).filter_by(code='SAFETY').one()
            for i,circle in enumerate(circles):
                people={}
                for j,code in enumerate(['CM','SUPERVISOR','GSM']):
                    employee=Employee(employee_no=str(9600000+i*10+j),name=f'排名合成{i}-{code}',attraction_id=circle.id,is_active=True)
                    db.add(employee);db.flush()
                    db.add(EmployeeRoleAssignment(employee_id=employee.id,role_id=roles[code].id,starts_on='2026-01-01',status='active',assignment_type='permanent'))
                    people[code]=employee
                for recipient_code,issuer_code,score in [('CM','SUPERVISOR','0.5'),('CM','GSM','1'),('SUPERVISOR','GSM','0.5')]:
                    recipient,issuer=people[recipient_code],people[issuer_code]
                    db.add(RecognitionRecord(employee_id=recipient.id,employee_no=recipient.employee_no,employee_name=recipient.name,
                        employee_role_snapshot=roles[recipient_code].name,employee_role_code_snapshot=recipient_code,
                        home_attraction_id=circle.id,home_attraction_name=circle.name,occurred_attraction_id=circle.id,
                        recognition_date=BASE['end_date'],recognition_month=BASE['end_date'][:7],recognition_type_id=kind.id,
                        recognition_type_name=kind.name,content='范围测试',recognizer_employee_id=issuer.id,recognizer_name=issuer.name,
                        recognizer_role_snapshot=roles[issuer_code].name,recognizer_role_code_snapshot=issuer_code,
                        operator_employee_id=issuer.id,operator_name=issuer.name,operator_role_snapshot=roles[issuer_code].name,
                        operator_role_code_snapshot=issuer_code,source='manager',status='confirmed',fraction=Decimal(score),credited_fraction=Decimal(score)))
            db.commit()
        yield c


def workbook(response):
    assert response.status_code==200,response.text
    return load_workbook(BytesIO(response.content))


@pytest.mark.parametrize('account',ACCOUNTS)
def test_hr_queries_and_exports_match_gsm_for_own_circle(client,account):
    c=client;login(c,account)
    me=c.get('/api/me').json();own=me['attraction_id']
    for population in ['frontline']:
        for category in CATEGORIES:
            params={**BASE,'population':population,'category':category}
            login(c,'GSMTEST01')
            expected=c.get('/api/pr-rankings',params={**params,'attraction_id':own})
            assert expected.status_code==200,expected.text
            gsm_wb=workbook(c.get('/api/pr-rankings/export',params={**params,'attraction_id':own}))
            login(c,account)
            actual=c.get('/api/pr-rankings',params=params)
            assert actual.status_code==200,actual.text
            expected_payload=expected.json()
            if category in {'leader','gsm_leader'}:
                expected_payload['circle_scope']='recipient'
            assert actual.json()==expected_payload
            explicit=c.get('/api/pr-rankings',params={**params,'attraction_id':own})
            assert explicit.json()==expected_payload
            hr_wb=workbook(c.get('/api/pr-rankings/export',params=params))
            if category in {'leader','gsm_leader'}:
                # Same synthetic same-circle rows; HR context explicitly restricts
                # recipients, while the manager context covers all signer venues.
                assert list(hr_wb.active.values)==list(gsm_wb.active.values)
                assert any('仅本圈CM/TR' in str(row) for row in hr_wb['导出说明'].values)
            else:
                assert [list(s.values) for s in hr_wb]==[list(s.values) for s in gsm_wb]
            assert me['employee_no'] in hr_wb.properties.description
            assert all(me['employee_no'] in s.oddHeader.center.text for s in hr_wb)
            hr_wb.close();gsm_wb.close()
    with SessionLocal() as db:
        audit=db.query(AuditLog).filter_by(action='导出PR排名',operator_id=me['id']).order_by(AuditLog.id.desc()).first()
        assert json.loads(audit.after_json)['attraction_id']==own


@pytest.mark.parametrize('account',ACCOUNTS)
def test_hr_cannot_query_export_or_enqueue_other_circles(client,account):
    from urllib.parse import urlencode
    c=client;login(c,account);me=c.get('/api/me').json()
    with SessionLocal() as db:
        other_ids=[r.id for r in db.query(Attraction).filter_by(active=True,employee_circle=True) if r.id!=me['attraction_id']]
    for other in [*other_ids,0,999999]:
        for category in CATEGORIES:
            params={**BASE,'attraction_id':other,'category':category}
            assert c.get('/api/pr-rankings',params=params).status_code==403
            assert c.get('/api/pr-rankings/export',params=params).status_code==403
            target='/api/pr-rankings/export?'+urlencode(params)
            assert c.post('/api/export-tasks',params={'target':target}).status_code==403
    # Last-value query semantics must match FastAPI's direct export route.
    params={**BASE,'attraction_id':me['attraction_id']}
    target='/api/pr-rankings/export?'+urlencode(params)+'&attraction_id='+str(other_ids[0])
    assert c.post('/api/export-tasks',params={'target':target}).status_code==403


@pytest.mark.parametrize('account',ACCOUNTS)
def test_hr_cannot_access_supervisor_performance_by_any_entry(client,account):
    from urllib.parse import urlencode
    c=client;login(c,account);own=c.get('/api/me').json()['attraction_id']
    for population in ['frontline','supervisor']:
        for category in CATEGORIES:
            if population=='frontline':continue
            params={**BASE,'attraction_id':own,'population':population,'category':category}
            assert c.get('/api/pr-rankings',params=params).status_code==403
            assert c.get('/api/pr-rankings/export',params=params).status_code==403
            assert c.post('/api/export-tasks',params={'target':'/api/pr-rankings/export?'+urlencode(params)}).status_code==403
    for suffix in ['&population=frontline&population=supervisor','&category=leader&population=supervisor','&category=gsm_leader&population=supervisor']:
        target='/api/pr-rankings/export?'+urlencode({**BASE,'attraction_id':own})+suffix
        assert c.post('/api/export-tasks',params={'target':target}).status_code==403


def test_old_supervisor_cache_cannot_bypass_current_hr_restrictions(client):
    from time import monotonic
    from app.routers import statistics
    c=client;login(c,'HR-HEAT');me=c.get('/api/me').json()
    key=(me['id'],('HR_CIRCLE',),BASE['start_date'],BASE['end_date'],'overall',None,'score','',1,20,me['attraction_id'],'supervisor')
    with statistics._pr_ranking_cache_lock:
        statistics._pr_ranking_response_cache[key]=(monotonic()+600,b'{"old_supervisor_data":true}')
    try:
        response=c.get('/api/pr-rankings',params={'start_date':BASE['start_date'],'end_date':BASE['end_date'],'population':'supervisor'})
        assert response.status_code==403 and 'old_supervisor_data' not in response.text
    finally:
        with statistics._pr_ranking_cache_lock:statistics._pr_ranking_response_cache.pop(key,None)


@pytest.mark.parametrize('category',['leader','gsm_leader'])
def test_hr_background_issuer_statistics_keep_counts_and_issued_scores(client,category):
    from urllib.parse import urlencode
    c=client;login(c,'HR-HEAT')
    params={**BASE,'category':category,'population':'frontline'}
    expected=workbook(c.get('/api/pr-rankings/export',params=params))
    response=c.post('/api/export-tasks',params={'target':'/api/pr-rankings/export?'+urlencode(params)})
    assert response.status_code==202,response.text
    jid=response.json()['id'];finished=None
    for _ in range(200):
        job=next(r for r in c.get('/api/export-tasks').json()['items'] if r['id']==jid)
        if job['status'] in {'completed','failed'}:finished=job;break
        time.sleep(.05)
    assert finished and finished['status']=='completed',finished
    actual=workbook(c.get(finished['download_url']))
    assert [list(s.values) for s in actual]==[list(s.values) for s in expected]
    headers=[cell.value for cell in actual.active[1]]
    assert '认可次数（总数）' in headers and '累计加分' in headers
    assert actual.active.max_row>1
    actual.close();expected.close()


def test_scope_is_revalidated_before_cache_and_completed_download(client):
    from urllib.parse import urlencode
    c=client;login(c,'HR-HEAT');me=c.get('/api/me').json();own=me['attraction_id']
    assert c.get('/api/pr-rankings',params=BASE).json()['attraction_id']==own
    target='/api/pr-rankings/export?'+urlencode(BASE)
    response=c.post('/api/export-tasks',params={'target':target});assert response.status_code==202,response.text
    jid=response.json()['id'];finished=None
    for _ in range(200):
        job=next(r for r in c.get('/api/export-tasks').json()['items'] if r['id']==jid)
        if job['status'] in {'completed','failed'}:finished=job;break
        time.sleep(.05)
    assert finished and finished['status']=='completed',finished
    download=finished['download_url']
    assert c.get(download).status_code==200
    # Previously completed tasks must obey the narrowed population/category rules too.
    from app.routers import export_tasks
    job=export_tasks._jobs[jid];original_target=job.target
    try:
        for suffix in ['&population=supervisor','&category=leader&population=supervisor','&category=gsm_leader&population=supervisor']:
            job.target=original_target+suffix
            assert c.get(download).status_code==403
    finally:job.target=original_target
    assert c.get(download).status_code==200
    with SessionLocal() as db:
        other=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.active.is_(True),Attraction.id!=own).first().id
    try:
        for invalid_flag in ['active','employee_circle']:
            with SessionLocal() as db:
                setattr(db.get(Attraction,own),invalid_flag,False);db.commit()
            assert c.get('/api/pr-rankings',params=BASE).status_code==403
            assert c.get('/api/pr-rankings/export',params=BASE).status_code==403
            assert c.get(download).status_code==403
            with SessionLocal() as db:
                setattr(db.get(Attraction,own),invalid_flag,True);db.commit()
        with SessionLocal() as db:
            db.get(Employee,me['id']).attraction_id=other;db.commit()
        assert c.get('/api/pr-rankings',params=BASE).json()['attraction_id']==other
        assert c.get('/api/pr-rankings',params={**BASE,'attraction_id':own}).status_code==403
        assert c.get(download).status_code==403
        with SessionLocal() as db:
            db.get(Employee,me['id']).attraction_id=None;db.commit()
        assert c.get('/api/pr-rankings',params=BASE).status_code==403
        assert c.get('/api/pr-rankings/export',params=BASE).status_code==403
    finally:
        with SessionLocal() as db:
            db.get(Employee,me['id']).attraction_id=own
            circle=db.get(Attraction,own);circle.active=True;circle.employee_circle=True;db.commit()
    assert c.get(download).status_code==200
    login(c,'HR-DWARF');assert c.get(download).status_code==404


def test_other_roles_keep_existing_ranking_and_export_access(client):
    c=client
    for account in ['CMTEST01','TRTEST01','TATEST01','SUPTEST01','HR01']:
        login(c,account)
        assert c.get('/api/pr-rankings',params=BASE).status_code==403
        assert c.get('/api/pr-rankings/export',params=BASE).status_code==403
    for account in ['GSMTEST01','AMTEST01','OMTEST01','TAGSMTEST01']:
        login(c,account)
        response=c.get('/api/pr-rankings',params=BASE)
        assert response.status_code==200 and response.json()['attraction_id'] is None
        exported=c.get('/api/pr-rankings/export',params=BASE)
        assert exported.status_code==(403 if account=='TAGSMTEST01' else 200)
