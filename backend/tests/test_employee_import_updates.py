"""Explicit per-row import updates, permission boundaries and atomic confirmation."""
import json
from datetime import date, timedelta

import pytest
from test_employee_import_conflicts import seed, circle_id, snapshot
from test_employee_types import setup, app, TestClient, SessionLocal, login, book
from app.v2_models import Employee, UserAccount, UserSession, AuditLog


def post(client, files, **data):
    return client.post('/api/hr/import-employees', files=files, data=data)


def preview(client, files, choices=None):
    response=post(client,files,preview='true',**({'decisions':json.dumps(choices)} if choices is not None else {}))
    assert response.status_code==200,response.text
    return response.json()


def commit(client, files, batch, choices):
    return post(client,files,expected_digest=batch['digest'],decisions=json.dumps(choices))


def field_values(employee_id):
    with SessionLocal() as db:
        e=db.get(Employee,employee_id)
        return e.name,e.employee_type,e.internship_ends_on


@pytest.mark.parametrize('mode',['system','excel','custom'])
def test_name_choice_updates_only_allowed_fields_and_audits(mode):
    no=str(9861000+['system','excel','custom'].index(mode))
    eid=seed(no,circle_id('矮人迷宫'));before=snapshot(eid)
    files=book([[no,'Excel新姓名','TR','矮人迷宫','Intern','2099-12-31']])
    choice=[{'row':2,'action':'update','name_mode':mode,'custom_name':'自定义姓名'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF')
        initial=preview(c,files);assert initial['duplicates'][0]['can_update'] and initial['duplicates'][0]['name_conflict']
        batch=preview(c,files,choice);assert batch['would_update']==1
        assert field_values(eid)==(before[0],'Comp28',None)
        assert commit(c,files,batch,choice).status_code==200
    expected=before[0] if mode=='system' else 'Excel新姓名' if mode=='excel' else '自定义姓名'
    assert field_values(eid)==(expected,'Intern','2099-12-31')
    after=snapshot(eid)
    assert after[1:3]==before[1:3] and after[4:]==before[4:]
    with SessionLocal() as db:
        audit=db.query(AuditLog).filter_by(entity_id=eid,action='Excel更新员工').one()
        assert expected in audit.after_json and before[0] in audit.before_json


def test_equal_name_blank_fields_keep_original_and_explicit_clear_is_separate():
    no='9861010';eid=seed(no,circle_id('矮人迷宫'))
    with SessionLocal() as db:
        e=db.get(Employee,eid);e.employee_type='Intern';e.internship_ends_on='2099-11-30';name=e.name;db.commit()
    files=book([[no,name,'CM','矮人迷宫','','']])
    choice=[{'row':2,'action':'update'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files,choice)
        assert batch['would_update']==0 and batch['unchanged']==1 and not batch['duplicates'][0]['name_conflict']
        assert commit(c,files,batch,choice).status_code==200
        assert field_values(eid)==(name,'Intern','2099-11-30')
        choice[0]['clear_end']=True;batch=preview(c,files,choice)
        assert batch['updates'][0]['changes']=={'internship_ends_on':{'before':'2099-11-30','after':None}}
        assert commit(c,files,batch,choice).status_code==200
        assert field_values(eid)==(name,'Intern',None)
        choice[0]['clear_type']=True;batch=preview(c,files,choice)
        assert commit(c,files,batch,choice).status_code==200
        assert field_values(eid)==(name,None,None)


def test_type_change_away_from_intern_clears_old_date_without_enabling_account():
    no='9861020';eid=seed(no,circle_id('矮人迷宫'));before=snapshot(eid)
    with SessionLocal() as db:
        e=db.get(Employee,eid);e.employee_type='Intern';e.internship_ends_on='2020-01-01';db.commit()
    files=book([[no,before[0],'CM','矮人迷宫','Comp40','2020-01-01']]);choice=[{'row':2,'action':'update'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files,choice)
        assert commit(c,files,batch,choice).status_code==200
    assert field_values(eid)==(before[0],'Comp40',None)
    assert snapshot(eid)[6]==before[6]


@pytest.mark.parametrize('kind',['foreign','manager','deleted','login_only'])
def test_unauthorized_update_is_rejected_and_cannot_create_other_rows(kind):
    index=['foreign','manager','deleted','login_only'].index(kind);no=str(9861030+index*10);fresh=str(int(no)+1)
    eid=seed(str(int(no)+2) if kind=='login_only' else no,circle_id('热力追踪' if kind=='foreign' else '矮人迷宫'),
             role='GSM' if kind=='manager' else 'CM',deleted=kind=='deleted',login_no=no)
    before=snapshot(eid)
    files=book([[no,'覆盖不可操作的人','CM','矮人迷宫','Intern','2099-12-31'],[fresh,'不应新增','TR','矮人迷宫','','']])
    choices=[{'row':2,'action':'update','name_mode':'excel'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');initial=preview(c,files)
        assert not initial['duplicates'][0]['can_update']
        if kind=='foreign':assert initial['duplicates'][0]['attraction_name']=='热力追踪'
        response=commit(c,files,initial,choices);assert response.status_code==403,response.text
    assert snapshot(eid)==before
    with SessionLocal() as db:assert not db.query(Employee).filter_by(employee_no=fresh).first()


def test_changed_values_or_choices_since_preview_require_new_confirmation():
    no='9861080';eid=seed(no,circle_id('矮人迷宫'))
    files=book([[no,'新名字','CM','矮人迷宫','Comp40','']]);choices=[{'row':2,'action':'update','name_mode':'excel'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');initial=preview(c,files)
        assert commit(c,files,initial,choices).status_code==409
        batch=preview(c,files,choices)
        with SessionLocal() as db:db.get(Employee,eid).employee_type='Comp36';db.commit()
        assert commit(c,files,batch,choices).status_code==409
        batch=preview(c,files,choices)
        assert commit(c,files,batch,[{'row':2,'action':'skip'}]).status_code==409


def test_expired_update_warns_and_revokes_sessions_but_preserves_other_fields():
    no='9861090';eid=seed(no,circle_id('矮人迷宫'))
    with SessionLocal() as db:
        account=db.query(UserAccount).filter_by(employee_id=eid).one();account.enabled=True;account.disabled_at=None
        account_id=account.id
        db.add(UserSession(account_id=account.id,token_hash='isolated-import-session',expires_at=__import__('datetime').datetime(2099,1,1)))
        db.commit()
    files=book([[no,'到期人员','CM','矮人迷宫','Intern',(date.today()-timedelta(days=2)).isoformat()]])
    choice=[{'row':2,'action':'update'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files,choice)
        assert len(batch['expired'])==1 and batch['expired'][0]['action']=='update'
        assert commit(c,files,batch,choice).status_code==200
    with SessionLocal() as db:
        account=db.get(UserAccount,account_id);assert not account.enabled and account.disabled_at
        assert not db.query(UserSession).filter_by(account_id=account_id).first()


def test_update_and_new_rows_and_audits_roll_back_together_on_failed_commit(monkeypatch):
    no='9861100';fresh='9861101';eid=seed(no,circle_id('矮人迷宫'));before=snapshot(eid)
    files=book([[no,'新姓名','CM','矮人迷宫','Comp40',''],[fresh,'新建合成人员','TR','矮人迷宫','','']])
    choice=[{'row':2,'action':'update','name_mode':'excel'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files,choice)
        from sqlalchemy.orm import Session
        original=Session.commit
        def fail(db):
            if any(isinstance(obj,AuditLog) and obj.action=='Excel导入员工' for obj in db.new):raise RuntimeError('import update commit failed')
            return original(db)
        with monkeypatch.context() as m:
            m.setattr(Session,'commit',fail)
            with pytest.raises(RuntimeError,match='commit failed'):commit(c,files,batch,choice)
    assert snapshot(eid)==before
    with SessionLocal() as db:
        assert not db.query(Employee).filter_by(employee_no=fresh).first()
        assert not db.query(AuditLog).filter_by(entity_id=eid,action='Excel更新员工').first()


@pytest.mark.parametrize('choices',[{},[{'row':True,'action':'update'}],[{'row':2,'action':'update','clear_type':'true'}],
    [{'row':2,'action':'update'},{'row':2,'action':'skip'}],[{'row':500,'action':'update'}]])
def test_invalid_choices_never_write(choices):
    no='9861110'
    with SessionLocal() as db:exists=db.query(Employee).filter_by(employee_no=no).first()
    if not exists:seed(no,circle_id('矮人迷宫'))
    files=book([[no,'新名字','CM','矮人迷宫','Comp40','']])
    with TestClient(app) as c:
        login(c,'HR-DWARF');response=post(c,files,preview='true',decisions=json.dumps(choices))
        assert response.status_code==400,response.text


def test_same_name_row_cannot_be_used_to_rename_by_forged_choice():
    no='9861120';eid=seed(no,circle_id('矮人迷宫'));before=snapshot(eid)
    files=book([[no,before[0],'CM','矮人迷宫','Comp40','']])
    choice=[{'row':2,'action':'update','name_mode':'custom','custom_name':'不能从同名行改名'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF')
        assert post(c,files,preview='true',decisions=json.dumps(choice)).status_code==400
    assert snapshot(eid)==before


def test_keep_system_name_preserves_legacy_whitespace_exactly():
    no='9861130';eid=seed(no,circle_id('矮人迷宫'))
    with SessionLocal() as db:db.get(Employee,eid).name=' 保留旧姓名 ';db.commit()
    files=book([[no,'文件其他名字','CM','矮人迷宫','Comp40','']]);choice=[{'row':2,'action':'update','name_mode':'system'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files,choice)
        assert batch['would_rename']==0
        assert commit(c,files,batch,choice).status_code==200
    assert field_values(eid)[0]==' 保留旧姓名 '
