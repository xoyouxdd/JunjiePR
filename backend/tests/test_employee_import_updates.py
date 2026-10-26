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


@pytest.mark.parametrize('choice',[{'row':2,'action':'update'}, {'row':2,'action':'update','name_mode':'excel'}, {'row':2,'action':'update','name_mode':'custom','custom_name':'新姓名'}])
def test_name_conflict_can_only_skip_even_with_forged_choice(choice):
    no=str(9861000+len(choice));eid=seed(no,circle_id('矮人迷宫'));before=snapshot(eid)
    files=book([[no,'Excel新姓名','TR','矮人迷宫','Intern','2099-12-31']])
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files)
        row=batch['duplicates'][0];assert not row['can_update'] and row['name_conflict']
        assert '系统员工管理' in row['reason']
        response=post(c,files,preview='true',decisions=json.dumps([choice]));assert response.status_code in (400,403)
        skip=[{'row':2,'action':'skip'}];batch=preview(c,files,skip);assert commit(c,files,batch,skip).status_code==200
    assert snapshot(eid)==before


@pytest.mark.parametrize('kind,end',[('', ''),('Intern',''),('Intern','2099-12-31')])
def test_overwrite_uses_file_values_including_blank(kind,end):
    no=str(9861010+['','Intern'].index(kind)+(1 if end else 0));eid=seed(no,circle_id('矮人迷宫'))
    with SessionLocal() as db:
        e=db.get(Employee,eid);e.employee_type='Intern';e.internship_ends_on='2099-11-30';name=e.name;db.commit()
    files=book([[no,name,'CM','矮人迷宫',kind,end]]);choice=[{'row':2,'action':'update'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files,choice);assert batch['would_update']==1
        assert commit(c,files,batch,choice).status_code==200
    assert field_values(eid)==(name,kind or None,end or None)


def test_equal_file_is_automatically_unchanged_without_selection():
    no='9861015';eid=seed(no,circle_id('矮人迷宫'));name=field_values(eid)[0]
    files=book([[no,name,'CM','矮人迷宫','Comp28','']])
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files)
        assert batch['unchanged']==1 and batch['would_skip']==0 and batch['would_update']==0
        assert batch['duplicates'][0]['unchanged'] and batch['duplicates'][0]['changes']=={}
        assert commit(c,files,batch,[]).status_code==200
    assert field_values(eid)==(name,'Comp28',None)


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
    choices=[{'row':2,'action':'update'}]
    with TestClient(app) as c:
        login(c,'HR-DWARF');initial=preview(c,files)
        assert not initial['duplicates'][0]['can_update']
        if kind=='foreign':assert initial['duplicates'][0]['attraction_name']=='热力追踪'
        response=commit(c,files,initial,choices);assert response.status_code==403,response.text
    assert snapshot(eid)==before
    with SessionLocal() as db:assert not db.query(Employee).filter_by(employee_no=fresh).first()


def test_changed_values_or_choices_since_preview_require_new_confirmation():
    no='9861080';eid=seed(no,circle_id('矮人迷宫'))
    files=book([[no,field_values(eid)[0],'CM','矮人迷宫','Comp40','']]);choices=[{'row':2,'action':'update'}]
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
    files=book([[no,field_values(eid)[0],'CM','矮人迷宫','Intern',(date.today()-timedelta(days=2)).isoformat()]])
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
    files=book([[no,before[0],'CM','矮人迷宫','Comp40',''],[fresh,'新建合成人员','TR','矮人迷宫','','']])
    choice=[{'row':2,'action':'update'}]
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
    eid=exists.id if exists else seed(no,circle_id('矮人迷宫'))
    files=book([[no,field_values(eid)[0],'CM','矮人迷宫','Comp40','']])
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


def test_legacy_name_whitespace_mismatch_requires_system_edit():
    no='9861130';eid=seed(no,circle_id('矮人迷宫'))
    with SessionLocal() as db:db.get(Employee,eid).name=' 保留旧姓名 ';db.commit()
    files=book([[no,'保留旧姓名','CM','矮人迷宫','Comp40','']])
    with TestClient(app) as c:
        login(c,'HR-DWARF');batch=preview(c,files)
        assert not batch['duplicates'][0]['can_update'] and batch['duplicates'][0]['name_conflict']
        assert post(c,files,preview='true',decisions=json.dumps([{'row':2,'action':'update'}])).status_code==403
    assert field_values(eid)[0]==' 保留旧姓名 '
