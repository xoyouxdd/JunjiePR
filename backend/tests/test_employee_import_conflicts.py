"""Skip-only occupied numbers, with the dwarf-circle HR and isolated fake data."""
from datetime import datetime, timedelta
import pytest

from test_employee_types import app, setup, login, book, TestClient, SessionLocal
from app.v2_crypto import hash_password
from app.v2_models import Employee, UserAccount, Role, EmployeeRoleAssignment, Attraction


def circle_id(name):
    with SessionLocal() as db:
        return db.query(Attraction).filter_by(name=name).one().id


def seed(no, circle, role='CM', *, deleted=False, login_no=None):
    with SessionLocal() as db:
        employee=Employee(employee_no=no,name='不可泄露原姓名'+no,attraction_id=circle,
                          is_active=not deleted,hired_on='2026-01-01',employee_type='Comp28',
                          account_deleted_at=datetime.now()-timedelta(days=10) if deleted else None)
        db.add(employee);db.flush()
        db.add(EmployeeRoleAssignment(employee_id=employee.id,
               role_id=db.query(Role).filter_by(code=role).one().id,
               starts_on='2026-01-01',status='active',assignment_type='permanent'))
        if not deleted:
            db.add(UserAccount(employee_id=employee.id,login_account=login_no or no,
                              password_hash=hash_password('isolated-import-only'),enabled=False,
                              disabled_at=datetime.now()-timedelta(days=5)))
        db.commit()
        return employee.id


def snapshot(employee_id):
    with SessionLocal() as db:
        employee=db.get(Employee,employee_id)
        account=db.query(UserAccount).filter_by(employee_id=employee_id).first()
        return (employee.name,employee.employee_no,employee.attraction_id,employee.employee_type,
                employee.account_deleted_at,employee.is_active,
                None if account is None else (account.login_account,account.password_hash,account.enabled,account.disabled_at),
                [(r.role_id,r.starts_on,r.ends_on,r.status) for r in db.query(EmployeeRoleAssignment).filter_by(employee_id=employee_id)])


@pytest.mark.parametrize('kind',['own','foreign','manager','login_only','deleted'])
def test_dwarf_hr_can_confirm_skip_without_disclosure_or_overwrite(kind):
    index=['own','foreign','manager','login_only','deleted'].index(kind)
    occupied=str(9851000+index*10);fresh=str(int(occupied)+1)
    own=circle_id('矮人迷宫');other=circle_id('热力追踪')
    employee_no=str(int(occupied)+2) if kind=='login_only' else occupied
    employee_id=seed(employee_no,other if kind=='foreign' else own,
                     'GSM' if kind=='manager' else 'CM',deleted=kind=='deleted',login_no=occupied)
    before=snapshot(employee_id)
    files=book([[occupied,'文件中已有名字','CM','矮人迷宫','Intern',''],
                [fresh,'矮人迷宫合成新员工','TR','矮人迷宫','Comp40','']])
    with TestClient(app) as client:
        login(client,'HR-DWARF')
        response=client.post('/api/hr/import-employees',files=files,data={'preview':'true'})
        assert response.status_code==200,response.text
        preview=response.json();assert preview['would_create']==1 and len(preview['duplicates'])==1
        item=preview['duplicates'][0]
        assert item['row']==2 and item['employee_no']==occupied and item['reason']
        if kind in {'manager','login_only'}:
            assert not item['can_update'] and 'current' not in item
            assert item['name']=='信息受限'
            assert before[0] not in response.text
        elif kind=='foreign':
            assert item['name']==before[0] and item['attraction_name']=='热力追踪' and not item['can_update']
            assert 'current' not in item and 'id' not in item
        else:
            assert item['name']==before[0] and item['id']==employee_id
        if kind=='deleted':assert '重新入职' in item['reason']
        with SessionLocal() as db:assert not db.query(Employee).filter_by(employee_no=fresh).first()
        for data in ({'expected_digest':preview['digest']},{'skip_existing':'true'}):
            assert client.post('/api/hr/import-employees',files=files,data=data).status_code==409
        result=client.post('/api/hr/import-employees',files=files,
                           data={'expected_digest':preview['digest'],'skip_existing':'true'})
        assert result.status_code==200,result.text
        assert result.json()['created']==1 and result.json()['skipped']==1
        assert result.json()['duplicates']==preview['duplicates']
        assert snapshot(employee_id)==before
        with SessionLocal() as db:
            new=db.query(Employee).filter_by(employee_no=fresh).one()
            assert new.attraction_id==own and new.employee_type=='Comp40'


def test_global_admin_can_skip_existing_different_circle_without_transfer():
    existing='9851100';fresh='9851101'
    employee_id=seed(existing,circle_id('热力追踪'));before=snapshot(employee_id)
    files=book([[existing,'不能覆盖','CM','矮人迷宫','',''],[fresh,'合成新增','CM','矮人迷宫','','']])
    with TestClient(app) as client:
        login(client)
        response=client.post('/api/hr/import-employees',files=files,data={'preview':'true'})
        assert response.status_code==200,response.text
        preview=response.json();assert preview['duplicates'][0]['name']==before[0]
        result=client.post('/api/hr/import-employees',files=files,
                           data={'skip_existing':'true','expected_digest':preview['digest']})
        assert result.status_code==200,result.text
        assert result.json()['created']==1 and snapshot(employee_id)==before


def test_scope_change_since_preview_requires_reconfirmation():
    employee_id=seed('9851110',circle_id('热力追踪'))
    files=book([['9851110','文件名字','CM','矮人迷宫','',''],['9851111','合成新增','CM','矮人迷宫','','']])
    with TestClient(app) as client:
        login(client,'HR-DWARF')
        preview=client.post('/api/hr/import-employees',files=files,data={'preview':'true'}).json()
        assert preview['duplicates'][0]['name']==snapshot(employee_id)[0] and not preview['duplicates'][0]['can_update']
        with SessionLocal() as db:
            db.get(Employee,employee_id).attraction_id=circle_id('矮人迷宫');db.commit()
        result=client.post('/api/hr/import-employees',files=files,
                           data={'skip_existing':'true','expected_digest':preview['digest']})
        assert result.status_code==409
        with SessionLocal() as db:assert not db.query(Employee).filter_by(employee_no='9851111').first()


def test_new_rows_still_reject_invalid_or_unauthorized_data_atomically():
    own=['9851120','合成本圈新增','CM','矮人迷宫','','']
    bad_rows=[['9851121','越权新员工','CM','热力追踪','',''],
              ['9851121','越权层级','GSM','矮人迷宫','',''],
              ['9851121','非法日期','TR','矮人迷宫','Intern','not-a-date'],own]
    with TestClient(app) as client:
        login(client,'HR-DWARF')
        for bad in bad_rows:
            result=client.post('/api/hr/import-employees',files=book([own,bad]),data={'preview':'true'})
            assert result.status_code==400,result.text
            with SessionLocal() as db:assert not db.query(Employee).filter_by(employee_no='9851120').first()
