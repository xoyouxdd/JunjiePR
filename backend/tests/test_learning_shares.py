"""Isolated library workflows, live authorization, stale revisions and shared material delivery."""
import os
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from io import BytesIO
import pytest
import fitz
from PIL import Image

os.environ['RECOGNITION_V2_DATA_DIR']=tempfile.mkdtemp(prefix='learning-test-')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='1234'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='HR123'
from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal, FILE_DIR
from app.v2_models import Attraction, Employee, Role, EmployeeActingDuty, EmployeeRoleAssignment, GroupMembership
from app.learning_models import LearningShare, LearningRevision, LearningAccess, LearningReaction, LearningAsset, LearningDraft, LearningFeedback


def login(c, no='TRTEST01'):
    r=c.post('/api/login',json={'employee_no':no,'password':'1234'})
    assert r.status_code==200,r.text


def ok(r):
    assert r.status_code==200,r.text
    return r.json()


@pytest.fixture
def client():
    with TestClient(app) as c:
        with SessionLocal() as db:
            for model in [LearningFeedback,LearningDraft,LearningRevision,LearningReaction,LearningAccess,LearningAsset,LearningShare]:db.query(model).delete()
            people={e.id:(e.attraction_id,e.is_active) for e in db.query(Employee)}
            db.commit()
        login(c)
        yield c
        with SessionLocal() as db:
            for id,(aid,active) in people.items():
                e=db.get(Employee,id);e.attraction_id=aid;e.is_active=active
            db.commit()


def create(c,**changes):
    return ok(c.post('/api/learning',json={'title':'交接攻略','body':'旧公开内容',**changes}))


def options(c):return ok(c.get('/api/learning/options'))


def other_circle(c):return next(x['id'] for x in options(c)['circles'] if not x['own'])


def move_share(id,aid):
    with SessionLocal() as db:
        db.get(LearningShare,id).circle_id=aid;db.commit()


def pdf():
    with fitz.open() as doc:
        doc.new_page().insert_text((20,30),'Learning shared material')
        return doc.tobytes()


def upload(c,raw=None):
    aid=next(x['id'] for x in options(c)['circles'] if x['own'])
    return ok(c.post('/api/learning/assets',data={'circle_id':aid},files={'file':('guide.pdf',raw or pdf(),'application/pdf')}))


def test_search_categories_and_literal_keywords(client):
    c=client;create(c,title='开场100%检查',category='开场准备');create(c,title='异常交接',category='异常处理')
    own=next(x['id'] for x in options(c)['circles'] if x['own'])
    rows=ok(c.get('/api/learning',params={'circle_id':own,'keyword':'%','category':'开场准备'}))['items']
    assert len(rows)==1 and rows[0]['category']=='开场准备'
    assert not ok(c.get('/api/learning',params={'circle_id':own,'keyword':'异常','category':'开场准备'}))['items']
    assert c.post('/api/learning',json={'title':'bad','body':'bad','category':'unknown'}).status_code==422


def test_private_drafts_stale_writes_publish_once_and_legacy_category(client):
    c=client;asset=upload(c)
    payload={'title':'私有草稿','body':'待发布内容','assets':[asset['id']],'category':'岗位操作'}
    d=ok(c.post('/api/learning/drafts',json=payload));id=d['id']
    assert ok(c.get('/api/learning/drafts'))['total']==1
    own=next(x['id'] for x in options(c)['circles'] if x['own'])
    assert not ok(c.get('/api/learning',params={'circle_id':own}))['items']
    login(c,'GSMTEST01')
    assert c.get(f'/api/learning/drafts/{id}').status_code==404
    assert c.get(f'/api/learning/assets/{asset["id"]}').status_code==403
    assert not ok(c.get('/api/learning/drafts'))['items']
    login(c)
    assert c.get(f'/api/learning/assets/{asset["id"]}').headers['cache-control']=='private, no-store'
    assert c.get(f'/api/learning/drafts/{id}').headers['cache-control']=='private, no-store'
    d=ok(c.put(f'/api/learning/drafts/{id}',json={**payload,'expected_version':1}))
    assert d['version']==2
    assert c.put(f'/api/learning/drafts/{id}',json={**payload,'expected_version':1}).status_code==409
    assert c.post(f'/api/learning/drafts/{id}/publish',json={'expected_version':1}).status_code==409
    published=ok(c.post(f'/api/learning/drafts/{id}/publish',json={'expected_version':2}))
    assert c.post(f'/api/learning/drafts/{id}/publish',json={'expected_version':2}).status_code==404
    assert ok(c.get('/api/learning',params={'circle_id':own}))['total']==1
    assert ok(c.get(f'/api/learning/{published["id"]}'))['category']=='岗位操作'


def test_revision_draft_preserves_approval_and_failed_publish_keeps_draft(client):
    c=client;s=create(c,category='开场准备');login(c,'GSMTEST01')
    payload={'title':'修改草稿','body':'主管修改','share_id':s['id'],'base_version':1,'category':'异常处理','summary':'核对分类'}
    d=ok(c.post('/api/learning/drafts',json=payload))
    result=ok(c.post(f'/api/learning/drafts/{d["id"]}/publish',json={'expected_version':1}))
    assert result['status']=='pending'
    assert ok(c.get(f'/api/learning/{s["id"]}'))['category']=='开场准备'
    stale=ok(c.post('/api/learning/drafts',json=payload))
    login(c)
    ok(c.post(f'/api/learning/{s["id"]}/revisions/{result["id"]}/decision',json={'action':'approve','expected_version':1}))
    assert ok(c.get(f'/api/learning/{s["id"]}'))['category']=='异常处理'
    login(c,'GSMTEST01')
    assert c.post(f'/api/learning/drafts/{stale["id"]}/publish',json={'expected_version':1}).status_code==409
    assert c.get(f'/api/learning/drafts/{stale["id"]}').status_code==200


def test_draft_revalidates_identity_and_incomplete_content(client):
    c=client;d=ok(c.post('/api/learning/drafts',json={'title':'只有标题'}))
    assert c.post(f'/api/learning/drafts/{d["id"]}/publish',json={'expected_version':1}).status_code==400
    assert c.get(f'/api/learning/drafts/{d["id"]}').status_code==200
    with SessionLocal() as db:
        tr=db.query(Employee).filter_by(employee_no='TRTEST01').one();tr.attraction_id=other_circle(c);db.commit()
    assert c.get(f'/api/learning/drafts/{d["id"]}').status_code==403
    login(c,'CMTEST02')
    assert c.post('/api/learning/drafts',json={'title':'无权限'}).status_code==403


def test_feedback_visibility_resolution_and_version_checks(client):
    c=client;s=create(c);id=s['id'];login(c,'CMTEST02')
    f=ok(c.post(f'/api/learning/{id}/feedback',json={'kind':'需要补充','content':'请补充第二步','expected_version':1}))
    assert len(ok(c.get(f'/api/learning/{id}/feedback'))['items'])==1
    assert c.post(f'/api/learning/{id}/feedback',json={'kind':'内容有误','content':' ','expected_version':1}).status_code==400
    assert c.post(f'/api/learning/{id}/feedback/{f["id"]}/resolve',json={'response':'处理','expected_version':1}).status_code==403
    login(c,'CMTEST01')
    assert not ok(c.get(f'/api/learning/{id}/feedback'))['items']
    login(c)
    rows=ok(c.get(f'/api/learning/{id}/feedback'));assert rows['can_manage'] and len(rows['items'])==1
    ok(c.post(f'/api/learning/{id}/feedback/{f["id"]}/resolve',json={'response':'已补充并核对','expected_version':1}))
    assert c.post(f'/api/learning/{id}/feedback/{f["id"]}/resolve',json={'response':'重复','expected_version':1}).status_code==409
    login(c,'CMTEST02')
    assert ok(c.get(f'/api/learning/{id}/feedback'))['items'][0]['response']=='已补充并核对'
    move_share(id,other_circle(c))
    assert c.get(f'/api/learning/{id}/feedback').status_code==403
    assert c.post(f'/api/learning/{id}/feedback',json={'kind':'内容有误','content':'无权限','expected_version':1}).status_code==403


def test_learning_category_upgrade_preserves_legacy_rows():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import Session
    from app.database.migrations.schema import ensure_learning_categories
    engine=create_engine('sqlite://')
    with Session(engine) as db:
        for table in ('learning_shares','learning_revisions'):
            db.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, body TEXT)'))
            db.execute(text(f"INSERT INTO {table}(body) VALUES ('原内容')"))
        ensure_learning_categories(db);ensure_learning_categories(db)
        for table in ('learning_shares','learning_revisions'):
            assert db.execute(text(f'SELECT body,category FROM {table}')).one()==('原内容','未分类')


def test_cross_circle_manager_can_draft_existing_material_without_publishing_privilege(client):
    c=client;s=create(c);target=other_circle(c);move_share(s['id'],target)
    login(c,'AMTEST01')
    d=ok(c.post('/api/learning/drafts',json={'title':'跨圈核对','body':'核对后的内容','summary':'补充步骤',
                                          'share_id':s['id'],'base_version':1,'category':'岗位操作'}))
    assert any(r['id']==d['id'] for r in ok(c.get('/api/learning/drafts'))['items'])
    assert c.get(f'/api/learning/drafts/{d["id"]}').status_code==200
    result=ok(c.post(f'/api/learning/drafts/{d["id"]}/publish',json={'expected_version':1}))
    assert result['status']=='pending'
    assert ok(c.get(f'/api/learning/{s["id"]}'))['body']=='旧公开内容'


def test_publish_scope_locked_metadata_and_no_todo_pollution(client):
    c=client;s=create(c)
    assert ok(c.get(f'/api/learning/{s["id"]}'))['viewer']['employee_no']=='TRTEST01'
    assert not any(x['type'].startswith('learning') for x in ok(c.get('/api/action-center'))['items'])
    login(c,'CMTEST02')
    assert c.post('/api/learning',json={'title':'no','body':'no'}).status_code==403
    assert ok(c.get(f'/api/learning/{s["id"]}'))['body']=='旧公开内容'
    target=other_circle(c);move_share(s['id'],target)
    listing=ok(c.get(f'/api/learning?circle_id={target}'))['items'][0]
    assert listing['locked'] and listing['title']=='交接攻略' and 'body' not in listing and 'assets' not in listing
    assert c.get(f'/api/learning/{s["id"]}').status_code==403
    assert c.put(f'/api/learning/{s["id"]}/reaction',json={'liked':True,'starred':True}).status_code==403


@pytest.mark.parametrize('account', ['TRTEST01','SUPTEST01','GSMTEST01','TATEST01','TAGSMTEST01'])
def test_publishers_create_and_confirm_only_in_their_home_circle(client, account):
    c = client
    login(c, account)
    home = next(circle['id'] for circle in options(c)['circles'] if circle['own'])
    other = other_circle(c)
    assert options(c)['can_publish']
    share = create(c, circle_id=other)
    draft = ok(c.post('/api/learning/drafts', json={'title':'新草稿','body':'待发布','circle_id':other}))
    published = ok(c.post(f'/api/learning/drafts/{draft["id"]}/publish', json={'expected_version':draft['version']}))
    path = f'/api/learning/{share["id"]}'
    for share_id in [share['id'], published['id']]:
        detail = ok(c.get(f'/api/learning/{share_id}'))
        assert detail['circle_id'] == home and detail['owner_id'] == detail['publisher_id']
        assert detail['can_review']
    editor = 'GSMTEST01' if account != 'GSMTEST01' else 'SUPTEST01'
    login(c, editor)
    revision = ok(c.post(path+'/revisions', json={'title':'修改','body':'新版','summary':'补充'}))
    assert revision['status'] == 'pending'
    decision = path+f'/revisions/{revision["id"]}/decision'
    assert c.post(decision, json={'action':'approve','expected_version':1}).status_code == 403
    assert ok(c.get(path))['body'] == '旧公开内容'
    login(c, account)
    ok(c.post(decision, json={'action':'approve','expected_version':1}))
    assert ok(c.post(path+'/revisions',json={'title':'负责人更新','body':'负责人新版','summary':'补充','expected_version':2}))['status'] == 'published'


@pytest.mark.parametrize('account', ['AMTEST01','OMTEST01','CMTEST02','HR01'])
def test_non_publishers_cannot_create_new_shares_or_drafts(client, account):
    c = client
    if account == 'HR01':
        ok(c.post('/api/login',json={'employee_no':account,'password':'HR123'}))
    else:
        login(c, account)
    assert not options(c)['can_publish']
    for path in ['/api/learning','/api/learning/drafts']:
        assert c.post(path,json={'title':'新攻略','body':'正文'}).status_code == 403


def test_expired_acting_owner_handover_and_cross_circle_limits(client):
    c = client
    home = next(circle['id'] for circle in options(c)['circles'] if circle['own'])
    other = other_circle(c)
    login(c,'TATEST01')
    first = create(c)
    path = f'/api/learning/{first["id"]}'
    publisher = ok(c.get(path))['owner_id']
    draft = ok(c.post('/api/learning/drafts',json={'title':'保留草稿','body':'待发布内容'}))
    with SessionLocal() as db:
        people = {e.employee_no:e.id for e in db.query(Employee).filter(Employee.employee_no.in_(
            ['SUPTEST01','GSMTEST01','TAGSMTEST01','TRTEST01']))}
        duty = db.query(EmployeeActingDuty).filter_by(employee_id=publisher).one()
        old_dates = duty.starts_on,duty.ends_on
        today = datetime.now().date()
        yesterday = (today-timedelta(days=1)).isoformat()
        duty.starts_on = today.isoformat()
        duty.ends_on = today.isoformat()
        duty_id = duty.id
        db.get(Employee,people['TRTEST01']).attraction_id = other
        db.commit()
    try:
        login(c,'SUPTEST01')
        revision = ok(c.post(path+'/revisions',json={'title':'修改','body':'交接后发布','summary':'补充'}))
        decision = path+f'/revisions/{revision["id"]}/decision'
        owners = f'/api/learning/owners?circle_id={home}'
        assert publisher in {employee['id'] for employee in ok(c.get(owners))['items']}
        login(c,'TATEST01')
        assert options(c)['can_publish'] and ok(c.get(path))['can_review']
        with SessionLocal() as db:
            duty = db.get(EmployeeActingDuty,duty_id)
            duty.starts_on = duty.ends_on = yesterday
            db.commit()
        assert not options(c)['can_publish'] and options(c)['pending_revisions'] == 0
        assert not ok(c.get(path))['can_review']
        assert c.post(decision,json={'action':'approve','expected_version':1}).status_code == 403
        assert c.post(f'/api/learning/drafts/{draft["id"]}/publish',json={'expected_version':1}).status_code == 403
        with SessionLocal() as db:
            assert db.get(LearningDraft,draft['id']).body == '待发布内容'
        login(c,'SUPTEST01')
        eligible = {employee['id'] for employee in ok(c.get(owners))['items']}
        assert {people['SUPTEST01'],people['GSMTEST01'],people['TAGSMTEST01']} <= eligible
        assert publisher not in eligible and people['TRTEST01'] not in eligible
        assert c.get(f'/api/learning/owners?circle_id={other}').status_code == 403
        for employee_id in [publisher, people['TRTEST01']]:
            assert c.post(path+'/transfer',json={'employee_id':employee_id,'reason':'交接','expected_version':1}).status_code == 400
        login(c,'TRTEST01')
        assert c.post(path+'/transfer',json={'employee_id':people['GSMTEST01'],'reason':'交接','expected_version':1}).status_code == 403
        assert c.post(path+'/revisions',json={'title':'跨圈','body':'正文','summary':'补充'}).status_code == 403
        login(c,'SUPTEST01')
        ok(c.post(path+'/transfer',json={'employee_id':people['GSMTEST01'],'reason':'代理到期','expected_version':1}))
        login(c,'GSMTEST01')
        detail = ok(c.get(path))
        assert detail['owner_id'] == people['GSMTEST01'] and detail['publisher_id'] == publisher
        assert detail['version'] == 1 and detail['body'] == '旧公开内容' and detail['can_review']
        ok(c.post(decision,json={'action':'approve','expected_version':1}))
        assert ok(c.get(path))['body'] == '交接后发布'
    finally:
        with SessionLocal() as db:
            duty = db.get(EmployeeActingDuty,duty_id)
            duty.starts_on,duty.ends_on = old_dates
            db.commit()


def test_expired_acting_gsm_keeps_base_supervisor_publication(client):
    c = client
    login(c,'TAGSMTEST01')
    share = create(c)
    path = f'/api/learning/{share["id"]}'
    with SessionLocal() as db:
        duty = db.query(EmployeeActingDuty).filter_by(employee_id=ok(c.get(path))['owner_id']).one()
        old_dates = duty.starts_on,duty.ends_on
        duty_id = duty.id
        duty.starts_on = duty.ends_on = (datetime.now().date()-timedelta(days=1)).isoformat()
        db.commit()
    try:
        assert ok(c.get('/api/me'))['role_code'] == 'SUPERVISOR'
        assert options(c)['can_publish'] and ok(c.get(path))['can_review']
        assert create(c)['id'] != share['id']
    finally:
        with SessionLocal() as db:
            duty = db.get(EmployeeActingDuty,duty_id)
            duty.starts_on,duty.ends_on = old_dates
            db.commit()


def test_collaborative_revision_owner_review_and_stale_version(client):
    c=client;s=create(c);id=s['id']
    login(c,'GSMTEST01')
    r=ok(c.post(f'/api/learning/{id}/revisions',json={'title':'新标题','body':'新公开内容','summary':'补充要点','expected_version':1}))
    assert r['status']=='pending'
    assert ok(c.get(f'/api/learning/{id}'))['body']=='旧公开内容'
    assert c.post(f'/api/learning/{id}/revisions/{r["id"]}/decision',json={'action':'approve','expected_version':1}).status_code==403
    login(c)
    assert options(c)['pending_revisions']==1
    assert not any(x['type'].startswith('learning') for x in ok(c.get('/api/action-center'))['items'])
    ok(c.post(f'/api/learning/{id}/revisions/{r["id"]}/decision',json={'action':'approve','expected_version':1}))
    assert ok(c.get(f'/api/learning/{id}'))['body']=='新公开内容'
    assert c.post(f'/api/learning/{id}/revisions/{r["id"]}/decision',json={'action':'approve','expected_version':2}).status_code==409
    assert c.post(f'/api/learning/{id}/revisions',json={'title':'旧版本','body':'不要覆盖','summary':'旧','expected_version':1}).status_code==409
    history=ok(c.get(f'/api/learning/{id}/revisions'))['items']
    assert any(x['body']=='旧公开内容' for x in history)


def test_reject_and_owner_direct_edit_and_reactions(client):
    c=client;s=create(c);id=s['id']
    login(c,'SUPTEST01')
    r=ok(c.post(f'/api/learning/{id}/revisions',json={'title':'修改','body':'待退回','summary':'建议'}))
    login(c)
    assert c.post(f'/api/learning/{id}/revisions/{r["id"]}/decision',json={'action':'reject','expected_version':1}).status_code==400
    ok(c.post(f'/api/learning/{id}/revisions/{r["id"]}/decision',json={'action':'reject','reason':'请核对','expected_version':1}))
    assert options(c)['pending_revisions']==0
    assert ok(c.post(f'/api/learning/{id}/revisions',json={'title':'本人修改','body':'直接发布','summary':'补充','expected_version':1}))['status']=='published'
    login(c,'CMTEST02')
    for _ in range(2):ok(c.put(f'/api/learning/{id}/reaction',json={'liked':True,'starred':True}))
    detail=ok(c.get(f'/api/learning/{id}'));assert detail['likes']==1 and detail['starred']
    own=next(x['id'] for x in options(c)['circles'] if x['own'])
    assert len(ok(c.get(f'/api/learning?circle_id={own}&view=star'))['items'])==1
    ok(c.put(f'/api/learning/{id}/reaction',json={'liked':False,'starred':False}))
    assert ok(c.get(f'/api/learning/{id}'))['likes']==0


def test_group_approval_expiry_revoke_and_shared_attachment(client):
    c=client;a=upload(c);s=create(c,assets=[a['id']]);id=s['id'];target=other_circle(c)
    with SessionLocal() as db:
        db.get(LearningAsset,a['id']).circle_id=target;db.commit()
    move_share(id,target)
    login(c,'CMTEST02')
    req={'circle_id':target,'reason':'多资质岗位复习'}
    ok(c.post('/api/learning/requests',json=req));ok(c.post('/api/learning/requests',json=req))
    assert c.get(f'/api/learning/{id}/assets/{a["id"]}').status_code==403
    login(c,'TATEST01')
    assert ok(c.get('/api/learning/requests'))['items']==[]
    login(c,'SUPTEST01')
    rows=ok(c.get('/api/learning/requests'))['items'];assert len(rows)==1
    assert any(x['type']=='learning_access' for x in ok(c.get('/api/action-center'))['items'])
    r=rows[0];before=datetime.now()
    approved=ok(c.post(f'/api/learning/requests/{r["id"]}/decision',json={'action':'approve','expected_version':r['generation']}))
    assert abs((datetime.fromisoformat(approved['expires_at'])-before-timedelta(days=7)).total_seconds())<3
    login(c,'CMTEST02');assert ok(c.get(f'/api/learning/{id}'))['expires_at']
    response=c.get(f'/api/learning/{id}/assets/{a["id"]}');assert response.status_code==200 and response.content==pdf_reference(a['id'])
    assert 'no-store' in response.headers['cache-control']
    assert c.post(f'/api/learning/{id}/revisions',json={'title':'no','body':'no','summary':'no'}).status_code==403
    with SessionLocal() as db:
        row=db.get(LearningAccess,r['id']);row.expires_at=datetime.now()-timedelta(seconds=1);db.commit()
    assert c.get(f'/api/learning/{id}').status_code==403
    assert c.get(f'/api/learning/{id}/assets/{a["id"]}').status_code==403
    ok(c.post('/api/learning/requests',json=req))
    login(c,'SUPTEST01');r=ok(c.get('/api/learning/requests'))['items'][0]
    ok(c.post(f'/api/learning/requests/{r["id"]}/decision',json={'action':'approve','expected_version':r['generation']}))
    active=ok(c.get('/api/learning/grants'))['items'][0]
    ok(c.post(f'/api/learning/requests/{active["id"]}/decision',json={'action':'revoke','reason':'已完成复习','expected_version':active['generation']}))
    login(c,'CMTEST02');assert c.get(f'/api/learning/{id}').status_code==403


def pdf_reference(id):
    with SessionLocal() as db:return (FILE_DIR/db.get(LearningAsset,id).key).read_bytes()


@pytest.mark.parametrize('account', ['SUPTEST01','GSMTEST01','TAGSMTEST01','AMTEST01','OMTEST01','HR01'])
def test_formal_managers_read_all_circles_without_expanding_edit_scope(client,account):
    c=client
    circle_rows=options(c)['circles']
    assert len(circle_rows)==3
    materials=[]
    for circle in circle_rows:
        asset=upload(c);share=create(c,assets=[asset['id']])
        with SessionLocal() as db:
            db.get(LearningShare,share['id']).circle_id=circle['id']
            db.get(LearningAsset,asset['id']).circle_id=circle['id'];db.commit()
        materials.append((circle['id'],share['id'],asset['id']))
    if account=='HR01':
        assert c.post('/api/login',json={'employee_no':'HR01','password':'HR123'}).status_code==200
    else:login(c,account)
    circles=options(c)['circles']
    assert all(row['readable'] and row['expires_at'] is None for row in circles)
    for circle_id,share_id,asset_id in materials:
        row=next(r for r in circles if r['id']==circle_id)
        listing=ok(c.get('/api/learning',params={'circle_id':circle_id}))
        assert not next(r for r in listing['items'] if r['id']==share_id)['locked']
        detail=ok(c.get(f'/api/learning/{share_id}'))
        assert detail['body']=='旧公开内容' and detail['expires_at'] is None
        assert c.get(f'/api/learning/{share_id}/assets/{asset_id}').content==pdf_reference(asset_id)
        if not row['own'] and account in {'SUPTEST01','GSMTEST01','TAGSMTEST01'}:
            assert not row['can_edit'] and not row['can_manage']
            assert not detail['can_edit'] and not detail['can_manage'] and not detail['can_publish']
            assert c.post(f'/api/learning/{share_id}/revisions',json={'title':'跨圈修改','body':'不应允许','summary':'越权测试'}).status_code==403
        assert c.post('/api/learning/requests',json={'circle_id':circle_id,'reason':'无需申请'}).status_code==409
    with SessionLocal() as db:
        employee=db.query(Employee).filter_by(employee_no=account).one()
        assert db.query(LearningAccess).filter_by(employee_id=employee.id).count()==0


@pytest.mark.parametrize('status', ['approved','revoked'])
def test_manager_read_is_not_expired_by_old_cross_circle_grant(client,status):
    c=client;s=create(c);target=other_circle(c);move_share(s['id'],target)
    login(c,'GSMTEST01')
    with SessionLocal() as db:
        employee=db.query(Employee).filter_by(employee_no='GSMTEST01').one()
        db.add(LearningAccess(employee_id=employee.id,circle_id=target,home_circle_id=employee.attraction_id,
            reason='历史授权',status=status,expires_at=datetime.now()-timedelta(days=1)))
        db.commit()
    row=next(r for r in options(c)['circles'] if r['id']==target)
    assert row['readable'] and row['expires_at'] is None and row['access_status'] is None
    assert ok(c.get(f'/api/learning/{s["id"]}'))['expires_at'] is None


@pytest.mark.parametrize('invalid_flag', ['active','employee_circle'])
def test_body_and_attachment_require_valid_circle_for_all_read_paths(client,invalid_flag):
    c=client;asset=upload(c);s=create(c,assets=[asset['id']]);target=other_circle(c)
    move_share(s['id'],target)
    with SessionLocal() as db:
        db.get(LearningAsset,asset['id']).circle_id=target
        frontline=db.query(Employee).filter_by(employee_no='CMTEST02').one()
        db.add(LearningAccess(employee_id=frontline.id,circle_id=target,home_circle_id=frontline.attraction_id,
            reason='有效跨圈授权',status='approved',expires_at=datetime.now()+timedelta(days=7)))
        db.commit()
    body_url=f'/api/learning/{s["id"]}'
    asset_url=f'/api/learning/{s["id"]}/assets/{asset["id"]}'
    # Cover permanent manager scope, a 7-day grant, and the employee's own circle.
    for account in ['SUPTEST01','GSMTEST01','TAGSMTEST01','AMTEST01','OMTEST01','HR01','CMTEST02','TRTEST01']:
        with SessionLocal() as db:
            employee=db.query(Employee).filter_by(employee_no=account).one()
            original_home=employee.attraction_id
            if account=='TRTEST01':employee.attraction_id=target
            db.commit()
        try:
            if account=='HR01':
                assert c.post('/api/login',json={'employee_no':'HR01','password':'HR123'}).status_code==200
            else:login(c,account)
            assert c.get(body_url).status_code==200
            response=c.get(asset_url)
            assert response.status_code==200 and response.content==pdf_reference(asset['id'])
            with SessionLocal() as db:
                setattr(db.get(Attraction,target),invalid_flag,False);db.commit()
            assert target not in {row['id'] for row in options(c)['circles']}
            assert c.get('/api/learning',params={'circle_id':target}).status_code==400
            assert c.get(body_url).status_code==403
            response=c.get(asset_url)
            assert response.status_code==403 and response.content!=pdf_reference(asset['id'])
        finally:
            with SessionLocal() as db:
                setattr(db.get(Attraction,target),invalid_flag,True)
                db.query(Employee).filter_by(employee_no=account).one().attraction_id=original_home
                db.commit()


@pytest.mark.parametrize('account', ['CMTEST02','TRTEST01','TATEST01'])
def test_frontline_and_acting_lead_still_need_cross_circle_approval(client,account):
    c=client;asset=upload(c);s=create(c,assets=[asset['id']]);target=other_circle(c);move_share(s['id'],target)
    login(c,account)
    row=next(r for r in options(c)['circles'] if r['id']==target)
    assert not row['readable']
    assert c.get(f'/api/learning/{s["id"]}').status_code==403
    assert c.get(f'/api/learning/{s["id"]}/assets/{asset["id"]}').status_code==403


@pytest.mark.parametrize('invalid_flag', ['active','employee_circle'])
@pytest.mark.parametrize('owner_account', ['TRTEST01','GSMTEST01'])
def test_private_draft_and_asset_require_valid_circle_without_breaking_drafts(client,invalid_flag,owner_account):
    c=client
    share=create(c)
    login(c,owner_account)
    asset=upload(c)
    payload={'title':'私有草稿','body':'未发布正文','category':'岗位操作','assets':[asset['id']],'summary':'补充岗位说明'}
    if owner_account=='GSMTEST01':payload['share_id']=share['id']
    draft=ok(c.post('/api/learning/drafts',json=payload))
    circle_id=next(row['id'] for row in options(c)['circles'] if row['own'])
    draft_url=f'/api/learning/drafts/{draft["id"]}'
    asset_url=f'/api/learning/assets/{asset["id"]}'
    assert ok(c.get(draft_url))['body']=='未发布正文'
    assert c.get(asset_url).content==pdf_reference(asset['id'])
    # Read-all managers still cannot inspect another employee's private draft or upload.
    for account in ['CMTEST02','TATEST01','SUPTEST01','AMTEST01','OMTEST01','HR01']:
        if account=='HR01':
            assert c.post('/api/login',json={'employee_no':'HR01','password':'HR123'}).status_code==200
        else:login(c,account)
        assert c.get(draft_url).status_code==404
        assert c.get(asset_url).status_code==403
    login(c,owner_account)
    with SessionLocal() as db:
        setattr(db.get(Attraction,circle_id),invalid_flag,False);db.commit()
    try:
        assert ok(c.get('/api/learning/drafts'))['total']==0
        assert c.get(draft_url).status_code==403
        response=c.get(asset_url)
        assert response.status_code==403 and response.content!=pdf_reference(asset['id'])
        assert c.put(draft_url,json={**payload,'expected_version':draft['version']}).status_code==403
        assert c.post(draft_url+'/publish',json={'expected_version':draft['version']}).status_code==403
        assert c.request('DELETE',draft_url,json={'expected_version':draft['version']}).status_code==403
        with SessionLocal() as db:
            row=db.get(LearningDraft,draft['id'])
            assert row.body=='未发布正文' and row.version==draft['version']
    finally:
        with SessionLocal() as db:
            setattr(db.get(Attraction,circle_id),invalid_flag,True);db.commit()
    assert ok(c.get(draft_url))['body']=='未发布正文'
    assert c.get(asset_url).content==pdf_reference(asset['id'])
    saved=ok(c.put(draft_url,json={**payload,'body':'恢复后更新','expected_version':draft['version']}))
    result=ok(c.post(draft_url+'/publish',json={'expected_version':saved['version']}))
    assert c.get(draft_url).status_code==404
    if owner_account=='GSMTEST01':
        assert result['status']=='pending'
        assert ok(c.get(f'/api/learning/{share["id"]}'))['body']=='旧公开内容'
    else:
        published=ok(c.get(f'/api/learning/{result["id"]}'))
        assert published['body']=='恢复后更新'
        assert c.get(f'/api/learning/{result["id"]}/assets/{asset["id"]}').content==pdf_reference(asset['id'])


def test_request_checks_live_group_and_identity(client):
    c=client;target=other_circle(c);login(c,'CMTEST02')
    ok(c.post('/api/learning/requests',json={'circle_id':target,'reason':'复习'}))
    login(c,'SUPTEST01');r=ok(c.get('/api/learning/requests'))['items'][0]
    with SessionLocal() as db:
        e=db.query(Employee).filter_by(employee_no='CMTEST02').one();e.attraction_id=target;db.commit()
    assert c.post(f'/api/learning/requests/{r["id"]}/decision',json={'action':'approve','expected_version':r['generation']}).status_code==403


def test_material_validation_private_upload_and_draft_acl(client):
    c=client;aid=next(x['id'] for x in options(c)['circles'] if x['own'])
    assert c.post('/api/learning/assets',data={'circle_id':aid},files={'file':('x.pdf',b'not pdf','application/pdf')}).status_code==400
    a=upload(c);s=create(c);id=s['id']
    assert c.get(f'/api/files/{a["id"]}').status_code in (403,404)
    assert c.get(f'/api/learning/{id}/assets/{a["id"]}').status_code==404
    login(c,'GSMTEST01');new=upload(c)
    r=ok(c.post(f'/api/learning/{id}/revisions',json={'title':'有附件','body':'正文','summary':'附件','assets':[new['id']]}))
    login(c,'CMTEST02')
    assert c.get(f'/api/learning/{id}/revisions/{r["id"]}/assets/{new["id"]}').status_code==403
    login(c)
    assert c.get(f'/api/learning/{id}/revisions/{r["id"]}/assets/{new["id"]}').status_code==200
    ok(c.post(f'/api/learning/{id}/revisions/{r["id"]}/decision',json={'action':'approve','expected_version':1}))
    login(c,'CMTEST02');assert c.get(f'/api/learning/{id}/assets/{new["id"]}').status_code==200


def test_manager_withdrawal_and_handover_preserve_pending(client):
    c=client;s=create(c);id=s['id']
    login(c,'GSMTEST01');r=ok(c.post(f'/api/learning/{id}/revisions',json={'title':'修改','body':'新内容','summary':'补充'}))
    with SessionLocal() as db:
        owner=db.query(Employee).filter_by(employee_no='TRTEST01').one();owner.is_active=False;db.commit()
        # Another existing test account becomes a TR for the isolated handover test.
        e=db.query(Employee).filter_by(employee_no='CMTEST02').one();tr=db.query(Role).filter_by(code='TR').one()
        assignment=db.query(EmployeeRoleAssignment).filter_by(employee_id=e.id).first();old_role=assignment.role_id;assignment.role_id=tr.id;target=e.id;assignment_id=assignment.id;db.commit()
    try:
        ok(c.post(f'/api/learning/{id}/transfer',json={'employee_id':target,'reason':'原发布人离职','expected_version':1}))
        login(c,'CMTEST02');assert ok(c.get(f'/api/learning/{id}'))['can_review']
        ok(c.post(f'/api/learning/{id}/revisions/{r["id"]}/decision',json={'action':'approve','expected_version':1}))
        assert c.post(f'/api/learning/{id}/withdraw',json={'action':'withdraw','reason':'x','expected_version':2}).status_code==403
        login(c,'SUPTEST01')
        ok(c.post(f'/api/learning/{id}/withdraw',json={'action':'withdraw','reason':'更新资料','expected_version':2}))
        assert c.get(f'/api/learning/{id}').status_code==403
    finally:
        with SessionLocal() as db:db.get(EmployeeRoleAssignment,assignment_id).role_id=old_role;db.commit()
