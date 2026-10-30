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
from app.v2_models import Employee, Role, EmployeeRoleAssignment, GroupMembership
from app.learning_models import LearningShare, LearningRevision, LearningAccess, LearningReaction, LearningAsset


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
            for model in [LearningRevision,LearningReaction,LearningAccess,LearningAsset,LearningShare]:db.query(model).delete()
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
