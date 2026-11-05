"""Real API/export regressions for signer scope, recipient identity and venues."""
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
import time
from urllib.parse import urlencode

import pytest
from openpyxl import load_workbook

os.environ['RECOGNITION_V2_DATA_DIR'] = tempfile.mkdtemp(prefix='issuer-rankings-')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = '1234'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'HR123'

from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal
from app.v2_crypto import hash_password
from app.v2_models import Attraction, Employee, EmployeeRoleAssignment, Role, RecognitionRecord, RecognitionType, UserAccount

DAY = date.today().isoformat()
BASE = {'start_date': DAY, 'end_date': DAY, 'page_size': 100}


def login(c, account='GSMTEST01'):
    c.post('/api/logout')
    r = c.post('/api/login', json={'employee_no': account, 'password': '1234'})
    assert r.status_code == 200, r.text


@pytest.fixture(scope='module')
def fixture():
    with TestClient(app) as c:
        with SessionLocal() as db:
            account = db.query(UserAccount).filter_by(login_account='HR-HEAT').one()
            account.enabled=True; account.must_change_password=False; account.password_hash=hash_password('1234')
            circles = {a.name: a for a in db.query(Attraction)}
            heat, dwarf, bear = [circles[n] for n in ['热力追踪', '矮人迷宫', '小熊罐子']]
            roles = {r.code: r for r in db.query(Role)}
            people = {}
            for i, (key, code, circle) in enumerate([
                ('lead', 'SUPERVISOR', heat), ('operator', 'SUPERVISOR', dwarf),
                ('gsm', 'GSM', heat), ('other_gsm', 'GSM', dwarf),
                ('ta', 'CM', heat), ('tagsm', 'SUPERVISOR', heat),
                ('cm', 'CM', dwarf), ('own_cm', 'TR', heat), ('recipient_lead', 'SUPERVISOR', dwarf),
            ]):
                p = Employee(employee_no=f'97000{i:02}', name='签卡测试' + key, attraction_id=circle.id, is_active=True)
                db.add(p); db.flush()
                db.add(EmployeeRoleAssignment(employee_id=p.id, role_id=roles[code].id,
                                              starts_on='2020-01-01', assignment_type='permanent', status='active'))
                people[key] = p
            unknown = Attraction(name='历史发生景点', active=False, recognition_venue=False, employee_circle=False)
            db.add(unknown); db.flush()
            kinds = {r.code: r for r in db.query(RecognitionType)}
            ids = {}

            def add(key, signer='gsm', target='cm', operator='operator', code='GSM',
                    kind='COURTESY', score='1', status='confirmed', venue=None, target_code=None,
                    role_label=None, day=DAY):
                p, t, o = people[signer], people[target], people[operator]
                row = RecognitionRecord(employee_id=t.id, employee_no=t.employee_no, employee_name=t.name,
                    employee_role_snapshot='测试', employee_role_code_snapshot=target_code or ('SUPERVISOR' if target in {'recipient_lead', 'tagsm'} else 'CM'),
                    home_attraction_id=t.attraction_id, home_attraction_name=circles['热力追踪'].name,
                    occurred_attraction_id=(venue or circles['小熊维尼历险记']).id,
                    recognition_date=day, recognition_month=day[:7], recognition_type_id=kinds[kind].id,
                    recognition_type_name=kinds[kind].name, content=key,
                    recognizer_employee_id=p.id, recognizer_name=p.name,
                    recognizer_role_snapshot=role_label or (roles[code].name if code else '历史认可人'),
                    recognizer_role_code_snapshot=code, operator_employee_id=o.id, operator_name=o.name,
                    operator_role_snapshot='主管', operator_role_code_snapshot='SUPERVISOR',
                    source='manager', fraction=Decimal('1.5'), credited_fraction=Decimal(score), status=status)
                db.add(row); db.flush(); ids[key] = row.id

            add('lead_cross', signer='lead', code='SUPERVISOR')
            add('lead_home', signer='lead', code='SUPERVISOR', target='own_cm', operator='lead', venue=heat)
            add('former_ta', signer='ta', code='TA_SUPERVISOR', target_code='CM', venue=circles['七个小矮人矿山车'])
            add('gsm_cm_zero', score='0', venue=circles['爱丽丝梦游仙境迷宫'])
            add('gsm_lead', target='recipient_lead', score='1.5')
            add('gsm_ta_recipient', target='ta', venue=heat)
            add('gsm_tagsm_recipient', target='tagsm', score='1.5')
            add('gsm_other', signer='other_gsm', target='own_cm', venue=heat)
            add('tagsm_cm', signer='tagsm', code='TA_GSM', target='own_cm', venue=heat)
            add('missing_venue', venue=unknown)
            add('pending', status='pending'); add('void', status='void'); add('rejected', status='rejected')
            add('fixed', code=None, kind='MSP', role_label='系统固定分值')
            add('custom_fixed', code=None, role_label='系统固定分值')
            add('qualification_only', signer='ta', code='TA_SUPERVISOR', role_label='临时主管认可资格')
            add('legacy', signer='lead', code=None, day=(date.today()-timedelta(days=1)).isoformat())
            db.commit()
            data = {'people': {k: p.id for k, p in people.items()}, 'numbers': {k: p.employee_no for k,p in people.items()},
                    'circles': [heat.id, dwarf.id, bear.id], 'records': ids, 'types': {k:v.id for k,v in kinds.items()}}
        login(c)
        yield c, data


def payload(fixture, category='gsm_leader', **params):
    c, data = fixture
    r = c.get('/api/pr-rankings', params={**BASE, 'category': category, **params})
    assert r.status_code == 200, r.text
    result = r.json()
    for row in result['rows']:
        assert row['count'] == sum(row['venue_counts'].values())
    return result


def selected(result, employee_id):
    return next(r for r in result['rows'] if r['employee_id'] == employee_id)


def test_lead_scope_includes_all_recipient_circles_and_actual_venues(fixture):
    _, d = fixture
    out = payload(fixture, 'leader', attraction_id=d['circles'][0])
    row = selected(out, d['people']['lead'])
    assert row['count'] == 2 and row['score'] == 2
    assert row['venue_counts'][str(d['circles'][0])] == 1
    assert row['venue_counts'][str(d['circles'][2])] == 1
    assert row['venue_counts'][str(d['circles'][1])] == 0  # recipient belongs here; occurrence does not
    assert selected(out, d['people']['ta'])['count'] == 1  # former acting duty retained
    assert all(r['employee_id'] != d['people']['operator'] for r in out['rows'])
    assert selected(payload(fixture, 'leader', attraction_id=d['circles'][1]), d['people']['operator'])['count'] == 0


@pytest.mark.parametrize('population,expected', [('frontline',3), ('supervisor',2), ('all',5)])
def test_gsm_scope_recipient_populations_and_signer_only(fixture, population, expected):
    _, d = fixture
    out = payload(fixture, population=population, attraction_id=d['circles'][0])
    row = selected(out, d['people']['gsm'])
    assert row['count'] == expected
    assert all(r['employee_id'] != d['people']['other_gsm'] for r in out['rows'])
    assert out['circle_scope'] == 'issuer'
    assert row['venue_counts'].get('other',0) == (0 if population=='supervisor' else 1)
    if population != 'supervisor':
        assert selected(out, d['people']['tagsm'])['count'] == 1
    if population == 'frontline':
        assert row['score'] == 2  # capped-to-zero record still counts


def test_types_search_dates_pagination_and_legacy_role(fixture):
    _, d = fixture
    out = payload(fixture, keyword=d['numbers']['gsm'], population='all')
    assert out['total'] == 1 and out['rows'][0]['count'] == 5
    assert not payload(fixture, keyword='%')['rows']
    assert not payload(fixture, subtype_id=d['types']['MSP'])['rows']
    assert payload(fixture, page=99)['rows'] == []
    yesterday = (date.today()-timedelta(days=1)).isoformat()
    old = payload(fixture, 'leader', start_date=yesterday, end_date=yesterday)
    assert selected(old, d['people']['lead'])['count'] == 1


def test_export_has_same_total_venues_population_and_background_result(fixture):
    c,d = fixture
    params={**BASE,'category':'gsm_leader','population':'all','keyword':d['numbers']['gsm']}
    out=payload(fixture, population='all', keyword=d['numbers']['gsm'])
    response=c.get('/api/pr-rankings/export',params=params)
    assert response.status_code==200,response.text
    wb=load_workbook(BytesIO(response.content));headers=list(next(wb.active.values));row=list(list(wb.active.values)[1])
    assert row[headers.index('认可次数（总数）')]==5
    assert sum(row[headers.index(v['name'])] for v in out['venue_columns'])==5
    assert row[headers.index('累计加分')]==5
    assert any('全部人群' in str(r) for r in wb['导出说明'].values)
    response=c.post('/api/export-tasks',params={'target':'/api/pr-rankings/export?'+urlencode(params)})
    assert response.status_code==202,response.text
    job_id=response.json()['id']
    for _ in range(200):
        job=next(j for j in c.get('/api/export-tasks').json()['items'] if j['id']==job_id)
        if job['status'] in {'completed','failed'}:break
        time.sleep(.05)
    assert job['status']=='completed',job
    downloaded=load_workbook(BytesIO(c.get(job['download_url']).content))
    assert [list(s.values) for s in downloaded]==[list(s.values) for s in wb]


def test_invalid_populations_and_hr_scope_are_not_bypassed(fixture):
    c,d=fixture
    for category in ['overall','recognition','deduction','absence','leader']:
        assert c.get('/api/pr-rankings',params={**BASE,'category':category,'population':'all'}).status_code==400
    assert c.get('/api/pr-rankings',params={**BASE,'category':'leader','population':'supervisor'}).status_code==400
    login(c,'HR-HEAT')
    try:
        out=payload(fixture, population='frontline')
        assert out['circle_scope']=='recipient'
        assert selected(out,d['people']['gsm'])['count']==1  # only own-circle CM/TR recipient
        assert selected(out,d['people']['other_gsm'])['count']==1  # outside signer, own-circle recipient
        for population in ['supervisor','all']:
            params={**BASE,'category':'gsm_leader','population':population}
            assert c.get('/api/pr-rankings',params=params).status_code==403
            assert c.get('/api/pr-rankings/export',params=params).status_code==403
            assert c.post('/api/export-tasks',params={'target':'/api/pr-rankings/export?'+urlencode(params)}).status_code==403
    finally:
        login(c)


def test_legacy_recipient_uses_event_date_and_snapshots_survive_role_changes(fixture):
    from app.data_cache import invalidate_data_caches
    c,d=fixture
    # Promote the recipient today, but the stored CM snapshot must keep the
    # earlier cards in CM/TR; the missing legacy snapshot resolves yesterday.
    with SessionLocal() as db:
        assignment=db.query(EmployeeRoleAssignment).filter_by(employee_id=d['people']['cm']).one()
        assignment.ends_on=(date.today()-timedelta(days=1)).isoformat()
        role=db.query(Role).filter_by(code='SUPERVISOR').one()
        promotion=EmployeeRoleAssignment(employee_id=d['people']['cm'],role_id=role.id,
                                         starts_on=DAY,status='active',assignment_type='permanent')
        db.add(promotion);db.flush();promotion_id=promotion.id
        legacy=db.get(RecognitionRecord,d['records']['legacy'])
        legacy.employee_role_code_snapshot=None
        db.commit()
    invalidate_data_caches()
    try:
        yesterday=(date.today()-timedelta(days=1)).isoformat()
        assert selected(payload(fixture,'leader',start_date=yesterday,end_date=yesterday),d['people']['lead'])['count']==1
        assert selected(payload(fixture,population='frontline'),d['people']['gsm'])['count']==3
        assert selected(payload(fixture,population='supervisor'),d['people']['gsm'])['count']==2
    finally:
        with SessionLocal() as db:
            db.query(EmployeeRoleAssignment).filter_by(id=promotion_id).delete()
            db.query(EmployeeRoleAssignment).filter_by(employee_id=d['people']['cm']).update({'ends_on':None})
            db.get(RecognitionRecord,d['records']['legacy']).employee_role_code_snapshot='CM'
            db.commit()
        invalidate_data_caches()


def test_withdrawal_updates_cached_total_and_venue_counts(fixture):
    from app.data_cache import invalidate_data_caches
    c,d=fixture
    # Make the current logged-in manager the original operator so withdrawal
    # travels through the real authorized API and cache invalidation path.
    with SessionLocal() as db:
        operator=db.query(Employee).filter_by(employee_no='GSMTEST01').one()
        record=db.get(RecognitionRecord,d['records']['gsm_cm_zero'])
        old_operator=record.operator_employee_id
        record.operator_employee_id=operator.id;db.commit()
    try:
        before=selected(payload(fixture,population='all'),d['people']['gsm'])
        r=c.delete(f"/api/recognitions/{d['records']['gsm_cm_zero']}")
        assert r.status_code==200,r.text
        after=selected(payload(fixture,population='all'),d['people']['gsm'])
        assert before['count']-after['count']==1
        assert before['score']==after['score']  # a zero-credit card still loses one count
        key=str(d['circles'][1])
        assert before['venue_counts'][key]-after['venue_counts'][key]==1
    finally:
        with SessionLocal() as db:
            record=db.get(RecognitionRecord,d['records']['gsm_cm_zero'])
            record.status='confirmed';record.operator_employee_id=old_operator;db.commit()
        invalidate_data_caches()


def test_signer_search_keeps_unicode_case_matching(fixture):
    from app.data_cache import invalidate_data_caches
    _,d=fixture
    with SessionLocal() as db:
        person=db.get(Employee,d['people']['gsm']);old_name=person.name
        person.name='ÉLODIE';db.commit()
    invalidate_data_caches()
    try:
        found=payload(fixture,population='all',keyword='élodie')
        assert found['total']==1
        assert found['rows'][0]['employee_id']==d['people']['gsm']
        assert found['rows'][0]['count']==5
    finally:
        with SessionLocal() as db:
            db.get(Employee,d['people']['gsm']).name=old_name;db.commit()
        invalidate_data_caches()


def test_pagination_and_export_cover_every_signer_without_duplication(fixture):
    from app.data_cache import invalidate_data_caches
    c,d=fixture
    record_ids=[];employee_ids=[]
    with SessionLocal() as db:
        template=db.get(RecognitionRecord,d['records']['gsm_cm_zero'])
        for i in range(12):
            person=Employee(employee_no=f'97800{i:02}',name=f'分页签卡测试{i}',
                            attraction_id=d['circles'][0],is_active=True)
            db.add(person);db.flush();employee_ids.append(person.id)
            values={column.name:getattr(template,column.name) for column in RecognitionRecord.__table__.columns
                    if column.name not in {'id','submitted_at'}}
            values.update(recognizer_employee_id=person.id,recognizer_name=person.name,
                          credited_fraction=Decimal('1'),fraction=Decimal('1'))
            record=RecognitionRecord(**values);db.add(record);db.flush();record_ids.append(record.id)
        db.commit()
    invalidate_data_caches()
    try:
        first=payload(fixture,keyword='分页签卡测试',page_size=10,page=1)
        second=payload(fixture,keyword='分页签卡测试',page_size=10,page=2)
        rows=first['rows']+second['rows']
        assert first['total']==second['total']==12
        assert first['pages']==second['pages']==2
        assert [r['rank'] for r in rows]==list(range(1,13))
        assert {r['employee_id'] for r in rows}==set(employee_ids)
        assert len({r['employee_id'] for r in rows})==len(rows)
        response=c.get('/api/pr-rankings/export',params={**BASE,'category':'gsm_leader','keyword':'分页签卡测试'})
        assert response.status_code==200,response.text
        wb=load_workbook(BytesIO(response.content))
        assert wb.active.max_row==13
        assert {row[1] for row in list(wb.active.values)[1:]}=={r['employee_no'] for r in rows}
    finally:
        with SessionLocal() as db:
            db.query(RecognitionRecord).filter(RecognitionRecord.id.in_(record_ids)).delete(synchronize_session=False)
            db.query(Employee).filter(Employee.id.in_(employee_ids)).delete(synchronize_session=False)
            db.commit()
        invalidate_data_caches()


def test_old_acting_snapshots_use_recipient_base_population(fixture):
    from app.data_cache import invalidate_data_caches
    _,d=fixture
    with SessionLocal() as db:
        a=db.get(RecognitionRecord,d['records']['gsm_ta_recipient'])
        b=db.get(RecognitionRecord,d['records']['gsm_tagsm_recipient'])
        old_a,old_b=a.employee_role_code_snapshot,b.employee_role_code_snapshot
        a.employee_role_code_snapshot='TA_SUPERVISOR';b.employee_role_code_snapshot='TA_GSM';db.commit()
    invalidate_data_caches()
    try:
        assert selected(payload(fixture,population='frontline'),d['people']['gsm'])['count']==3
        assert selected(payload(fixture,population='supervisor'),d['people']['gsm'])['count']==2
    finally:
        with SessionLocal() as db:
            db.get(RecognitionRecord,d['records']['gsm_ta_recipient']).employee_role_code_snapshot=old_a
            db.get(RecognitionRecord,d['records']['gsm_tagsm_recipient']).employee_role_code_snapshot=old_b
            db.commit()
        invalidate_data_caches()


def test_frozen_policy_distinguishes_real_signers_from_fixed_category_awards(fixture):
    import json
    from app.data_cache import invalidate_data_caches
    _,d=fixture
    with SessionLocal() as db:
        real=db.get(RecognitionRecord,d['records']['fixed'])
        fixed=db.get(RecognitionRecord,d['records']['custom_fixed'])
        old=[(r.id,r.recognizer_role_code_snapshot,r.recognizer_role_snapshot,r.scoring_policy_json) for r in [real,fixed]]
        for row in [real,fixed]:
            row.recognizer_role_code_snapshot='GSM';row.recognizer_role_snapshot='GSM'
        real.scoring_policy_json=json.dumps({'configured':True,'mode':'role'})
        fixed.scoring_policy_json=json.dumps({'configured':True,'mode':'fixed'})
        db.commit()
    invalidate_data_caches()
    try:
        result=payload(fixture,population='all',keyword=d['numbers']['gsm'])
        assert result['rows'][0]['count']==6  # real MSP signer counts, fixed custom award does not
        result=payload(fixture,population='all',subtype_id=d['types']['MSP'])
        assert selected(result,d['people']['gsm'])['count']==1
    finally:
        with SessionLocal() as db:
            for rid,code,label,policy in old:
                row=db.get(RecognitionRecord,rid)
                row.recognizer_role_code_snapshot=code;row.recognizer_role_snapshot=label;row.scoring_policy_json=policy
            db.commit()
        invalidate_data_caches()
