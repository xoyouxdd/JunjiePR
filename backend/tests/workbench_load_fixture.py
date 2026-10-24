"""Fifteen fictional TR identities, then the existing real synthetic-video fixture."""
import os, sys, runpy
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'backend'))
os.environ['RECOGNITION_V2_DATA_DIR']=str(Path(sys.argv[1])/'data')
os.environ['RECOGNITION_ENVIRONMENT']='test'
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='fixture2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='fixture-admin2026'
from app.v2_database import init_db,SessionLocal
from app.v2_models import Employee,Role,Attraction,EmployeeRoleAssignment,UserAccount
from app.v2_crypto import hash_password
init_db()
with SessionLocal() as db:
    aid=db.query(Attraction).filter_by(name='热力追踪').one().id
    role=db.query(Role).filter_by(code='TR').one()
    for i in range(15):
        no=str(9900100+i)
        if db.query(Employee).filter_by(employee_no=no).first():continue
        e=Employee(employee_no=no,name=f'合成并发读者{i+1}',attraction_id=aid,is_active=True,hired_on='2026-01-01')
        db.add(e);db.flush()
        db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=role.id,starts_on='2026-01-01',status='active',assignment_type='permanent'))
        db.add(UserAccount(employee_id=e.id,login_account=no,password_hash=hash_password('fixture2026'),enabled=True,must_change_password=False,credential_initialized=True))
    db.commit()
runpy.run_path(str(root/'backend/tests/video_learning_fixture.py'),run_name='__main__')
