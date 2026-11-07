"""Real account archive/registration endpoints with isolated synthetic identities."""
import os
import tempfile
from datetime import date, datetime, timedelta

os.environ['RECOGNITION_V2_DATA_DIR'] = tempfile.mkdtemp(prefix='medal-real-lifecycle-')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = 'isolated-test-only'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'isolated-admin-only'

from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal
from app.v2_models import Employee, UserAccount, AttendanceMedal, MedalAccountIdentity
from app.services import attendance_medals as medals
from app.services.employment import employment_history


def test_real_rebuild_archive_and_rehire_keep_identity_and_reset_only_rehire_month(monkeypatch):
    from app.routers import accounts, employees
    class January(date):
        @classmethod
        def today(cls): return cls(2027,1,31)
    class March(date):
        @classmethod
        def today(cls): return cls(2027,3,1)
    with TestClient(app) as client:
        logged=client.post('/api/login',json={'employee_no':'HR01','password':'isolated-admin-only'})
        assert logged.status_code==200,logged.text
        circle=client.get('/api/options').json()['employee_circles'][0]['id']
        body={'employee_no':'9817088','name':'勋章身份周期合成测试','role_code':'CM','attraction_id':circle,
              'hired_on':'2026-10-01','starts_on':'2026-10-01'}
        created=client.post('/api/hr/employees',json=body)
        assert created.status_code==200,created.text
        eid=created.json()['employee']['id']
        # Establish an explicit historical date through the audited repair API,
        # rather than relying on the machine's execution month.
        fixed=client.post('/api/attendance-medals/repair-identity',json={
            'employee_id':eid,'first_created_on':'2026-10-02','reason':'合成原账号日期台账'})
        assert fixed.status_code==200,fixed.text
        with SessionLocal() as db:
            person=db.get(Employee,eid)
            original=db.get(MedalAccountIdentity,eid).first_created_at
            account=db.query(UserAccount).filter_by(employee_id=eid).one()
            # Technical credential replacement does not archive/terminate a person.
            db.delete(account);db.flush()
            db.add(UserAccount(employee_id=eid,login_account=person.employee_no,password_hash='unused',
                created_at=datetime(2027,1,15),enabled=False,disabled_at=datetime.now()-timedelta(days=4)))
            db.add(AttendanceMedal(employee_id=eid,month='2027-01',attraction_id=circle))
            db.commit()
            assert db.get(MedalAccountIdentity,eid).first_created_at==original
            assert medals.qualification(db,person,'2027-01')[0]=='拟发放'
            assert not employment_history(person)
        monkeypatch.setattr(accounts,'date',January)
        removed=client.request('DELETE',f'/api/hr/employees/{eid}/account',json={'reason':'合成正式离职'})
        assert removed.status_code==200,removed.text
        with SessionLocal() as db:
            person=db.get(Employee,eid)
            assert person.terminated_on=='2027-01-31'
            assert db.get(MedalAccountIdentity,eid).first_created_at==original
            assert medals.qualification(db,person,'2027-01')[0]=='拟发放'
        monkeypatch.setattr(employees,'date',March)
        monkeypatch.setattr(medals,'date',March)
        rehired=client.post('/api/hr/employees',json={**body,'hired_on':'2027-03-01','starts_on':'2027-03-01'})
        assert rehired.status_code==200,rehired.text
        assert rehired.json()['rehired'] and rehired.json()['employee']['id']==eid
        with SessionLocal() as db:
            person=db.get(Employee,eid)
            assert employment_history(person)[-1]['rehired_on']=='2027-03-01'
            assert db.get(MedalAccountIdentity,eid).first_created_at==original
            assert medals.qualification(db,person,'2027-01')[0]=='拟发放'
            assert medals.qualification(db,person,'2027-02')[1]=='该月不在职'
            assert medals.qualification(db,person,'2027-03')[1]=='重新入职当月不参与'
            assert medals.qualification(db,person,'2027-04')[0]=='拟发放'
        public=client.get(f'/api/attendance-medals/employees/{eid}').json()
        assert public['count']==1 and public['items'][0]['month']=='2027-01'
