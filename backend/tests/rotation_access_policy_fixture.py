"""Disposable formal simulator, cross-circle roles and three untouched roster days."""
import os
from pathlib import Path
import sys
from datetime import date

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='rotation-local-2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='rotation-local-admin-2026'
from local_tools.rotation_clock.guard import prepare,exclusive_data

path=prepare(Path(sys.argv[1])/'data')
with exclusive_data(path):
    from app.main import app
    from local_tools.rotation_clock.extension import install
    from local_tools.rotation_clock.demo import seed
    install(app);seed(path)
    from app.v2_database import SessionLocal
    from app.v2_models import Attraction,Employee,Role,EmployeeRoleAssignment,UserAccount
    from app.v2_crypto import hash_password
    from app.rotation import service as S
    with SessionLocal() as db:
        heat=db.query(Attraction).filter_by(name='热力追踪').one()
        other=db.query(Attraction).filter_by(name='矮人迷宫').one()
        roles=[(str(9738100+i),code,other.id) for i,code in enumerate(['SUPERVISOR','TA_SUPERVISOR','GSM','TA_GSM'])]
        roles.extend([('9738110','TR',heat.id),('9738111','TR',other.id)])
        for no,code,aid in roles:
            e=Employee(employee_no=no,name='合成权限'+code,attraction_id=aid,is_active=True,hired_on=date.today().isoformat())
            db.add(e);db.flush()
            db.add(EmployeeRoleAssignment(employee_id=e.id,role_id=db.query(Role.id).filter_by(code=code).scalar(),
                       starts_on='2026-01-01',status='active',assignment_type='permanent'))
            db.add(UserAccount(employee_id=e.id,login_account=no,password_hash=hash_password('rotation-local-2026'),
                       enabled=True,must_change_password=False,credential_initialized=True))
        db.commit();db.info['rotation_formal']=True
        people={str(9720001+i):{'name':f'模拟伙伴{i+1:02d}','type':'','mark':''} for i in range(12)}
        shifts={no:'07:15-16:15' if i<8 else '16:00-00:30' if i<10 else '19:00-02:00' for i,no in enumerate(people)}
        for day in ['2026-11-07','2026-11-08','2026-11-09']:
            S.save_roster(db,heat.id,{'people':people,'days':{day:shifts},'hours':{}},scope='day',
                          file_name='隔离权限班表.xlsx',uploader_id=None,uploader_name='隔离测试')
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')
